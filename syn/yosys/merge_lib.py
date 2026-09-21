#!/usr/bin/env python3
"""Merge several Liberty files into one, because abc will only read one.

ASAP7 ships its standard cells split across five functional groups -- SIMPLE,
INVBUF, AO, OA and SEQ -- one Liberty file each. yosys's `abc` pass takes a
single `-liberty`, so they have to be merged before mapping.

THE THING THAT MAKES THIS NON-TRIVIAL, and that the first version of this
script got wrong: a cell's timing does not live in the cell. Every `timing()`
group names a `lu_table_template`, and those templates are declared in the
LIBRARY HEADER. Keeping the first file's header and appending everyone else's
cells produces a Liberty that parses, loads without error in yosys, and is
quietly missing the timing model for most of its cells -- because the templates
those cells reference were declared in the headers that were thrown away.

The symptom is not an error. It is a synthesis run where every target period
closes with exactly zero slack, because most gates have been modelled as
free. See `docs/synthesis.md`.

So: the merged header is the first file's, plus every named group from every
other file that the first one does not already declare -- templates, waveforms,
operating conditions, wire loads. Deduplicated by (kind, name), first
declaration wins. Then the cells.

`--check` (on by default) then verifies the result: every template a timing
group references must be declared in the merged file. That check is the whole
reason to trust the output.

Usage: merge_lib.py <out.lib> <in1.lib> <in2.lib> ...
"""
import re
import sys

# Header attributes that must agree across inputs. Two files from the same PDK
# and corner agree; mixing corners would misreport every cell after the first.
CRITICAL = ("time_unit", "voltage_unit", "current_unit",
            "capacitive_load_unit", "leakage_power_unit",
            "pulling_resistance_unit", "nom_voltage", "nom_temperature",
            "nom_process")

_ATTR = re.compile(r"^\s*(%s)\s*:?\s*\(?\s*([^;()]*)\s*\)?\s*;" %
                   "|".join(CRITICAL), re.M)

# A top-level group inside library(): `kind (name) {`
_GROUP = re.compile(r"^(\s*)([A-Za-z_][A-Za-z_0-9]*)\s*\(\s*([^)]*?)\s*\)\s*\{",
                    re.M)


def header_facts(text):
    facts = {}
    for m in _ATTR.finditer(text[:200000]):
        facts[m.group(1)] = m.group(2).strip().strip('"')
    return facts


def span_of_group(text, open_brace_idx):
    """Index just past the matching close brace, ignoring braces in strings."""
    depth = 0
    i = open_brace_idx
    in_str = False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise SystemExit("unbalanced braces -- is this a Liberty file?")


def library_body(text):
    """(text before the library's first top-level group, body, tail)."""
    m = re.search(r"^\s*library\s*\(", text, re.M)
    if not m:
        raise SystemExit("no library() group found")
    lb = text.index("{", m.start())
    end = span_of_group(text, lb)
    return text[:lb + 1], text[lb + 1:end - 1], text[end:]


def top_groups(body):
    """Every top-level group in the library body, as (kind, name, text)."""
    out = []
    i = 0
    while True:
        m = _GROUP.search(body, i)
        if not m:
            return out
        end = span_of_group(body, body.index("{", m.end() - 1))
        out.append((m.group(2), m.group(3), body[m.start():end]))
        i = end


def preamble(body, groups):
    """The scalar attributes before the first group -- units and defaults."""
    if not groups:
        return body
    first = body.index(groups[0][2])
    return body[:first]


def check_templates(text):
    declared = {name for kind, name, _ in top_groups(library_body(text)[1])
                if kind.endswith("template") or kind.endswith("waveform")}
    used = set(re.findall(
        r"(?:cell_rise|cell_fall|rise_transition|fall_transition|"
        r"rise_constraint|fall_constraint|rise_power|fall_power|power)\s*"
        r"\(\s*([A-Za-z_][A-Za-z_0-9]*)\s*\)", text))
    used.discard("scalar")
    missing = sorted(used - declared - {""})
    return missing


def main(out, inputs):
    if len(inputs) < 2:
        raise SystemExit("merge_lib.py needs at least two inputs")

    texts = [open(p).read() for p in inputs]
    ref = header_facts(texts[0])
    for path, text in zip(inputs[1:], texts[1:]):
        facts = header_facts(text)
        for k in CRITICAL:
            if k in ref and k in facts and ref[k] != facts[k]:
                raise SystemExit(
                    f"refusing to merge: {path} declares {k} = {facts[k]} but "
                    f"{inputs[0]} declares {ref[k]}. Mixing corners or PDKs in "
                    f"one Liberty misreports every cell after the first.")

    head, body0, _ = library_body(texts[0])
    groups0 = top_groups(body0)
    pre = preamble(body0, groups0)

    seen = set()
    support, cells = [], []
    for text in texts:
        _, body, _ = library_body(text)
        for kind, name, blob in top_groups(body):
            if kind == "cell":
                cells.append(blob)
            else:
                key = (kind, name)
                if key not in seen:
                    seen.add(key)
                    support.append(blob)

    merged = head + pre + "\n".join(support) + "\n" + "\n".join(cells) + "\n}\n"

    missing = check_templates(merged)
    if missing:
        raise SystemExit(
            "merged Liberty references templates nobody declares: "
            + ", ".join(missing[:8])
            + "\nThat is the bug this script exists to avoid -- the result "
              "would load without error and model those cells as free.")

    with open(out, "w") as f:
        f.write(merged)
    print(f"{out}: {len(cells)} cells, {len(support)} support groups, "
          f"from {len(inputs)} files "
          f"({ref.get('nom_voltage', '?')} V, "
          f"{ref.get('nom_temperature', '?')} C) -- templates check clean")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2:])
