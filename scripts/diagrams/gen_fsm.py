"""State machines, rendered from the SAME table the RTL is checked against.

Hand-drawing a state machine in a documentation tool is how documentation
starts lying. These diagrams are generated from `tb/models/tables.py` -- the
independent transcription that `test_protocol_l1_table.py` and
`test_protocol_dir_table.py` compare the RTL to, cell by cell. If the RTL and
the table disagree those tests fail; if the table changes, the picture changes
with it. So a diagram that is wrong is a diagram that could not have been
generated.

Output is Mermaid, injected between markers in docs/diagrams.md, because
GitHub renders it inline and a state machine is exactly the kind of graph
auto-layout is good at.
"""
import sys, os, re
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tb"))
from models import tables as T  # noqa: E402

DOC = os.path.join(ROOT, "docs", "diagrams.md")

L1_STABLE = ("I", "S", "E", "M")
L1_TRANSIENT_FILL = {
    # by what the transient is waiting for
    "waiting for data": ("IS_D", "IM_AD", "SM_AD"),
    "waiting for acks": ("IM_A", "SM_A"),
    "waiting for a Put-Ack": ("MI_A", "EI_A", "SI_A", "II_A"),
}


def _merge(edges):
    """Collapse parallel arcs so the picture has one line per state pair."""
    by_pair = defaultdict(list)
    for src, dst, lab in edges:
        by_pair[(src, dst)].append(lab)
    return [(s, d, "<br>".join(labs)) for (s, d), labs in by_pair.items()]


# The thirteen states split into two pictures. One diagram with all of them is
# a legible graph only in the sense that nothing overlaps: thirteen nodes and
# thirty-three arcs on one canvas is a picture of a mess, and the reader learns
# nothing from it.
#
# The split is not cosmetic -- it is the same one the states themselves make. A
# transient is either waiting for a line to arrive (IS_D, IM_AD, IM_A, SM_AD,
# SM_A) or waiting for permission to let one go (MI_A, EI_A, SI_A, II_A), and
# no transient is ever both. The four stable states appear in both halves,
# which is correct: they are where the two halves meet.
#
# A split diagram can lie by omission, so `_check_split_covers` fails the
# generator if any arc in the table appears in neither half.
L1_FETCH = ("I", "S", "E", "M", "IS_D", "IM_AD", "IM_A", "SM_AD", "SM_A")
L1_EVICT = ("I", "S", "E", "M", "MI_A", "EI_A", "SI_A", "II_A")


def _l1_edges():
    edges = []
    for (st, ev), (nxt, act) in sorted(T.L1_TABLE.items()):
        if nxt == st:
            continue
        edges.append((T.STATE_NAMES[st], T.STATE_NAMES[nxt],
                      T.EVENT_NAMES[ev]))
    # Two arcs are not table cells and would otherwise leave IM_A and SM_A
    # looking like dead ends. Completion is not an event: it is the
    # controller's condition `ack_cnt == 0 once the data has arrived`, which is
    # exactly the distinction race R1 turns on, so it is drawn and labelled as
    # what it is rather than quietly folded into Inv-Ack.
    edges.append(("IM_A", "M", "ack_cnt == 0 after data (retire)"))
    edges.append(("SM_A", "M", "ack_cnt == 0 after data (retire)"))
    return edges


def _check_split_covers(edges):
    for src, dst, lab in edges:
        in_f = src in L1_FETCH and dst in L1_FETCH
        in_e = src in L1_EVICT and dst in L1_EVICT
        if not (in_f or in_e):
            raise SystemExit(
                f"gen_fsm: the {src} -> {dst} arc ({lab}) falls in neither "
                f"half of the split. Fix L1_FETCH/L1_EVICT rather than the "
                f"diagram -- a split that drops an arc is worse than one big "
                f"picture.")


def l1_mermaid(half):
    keep = L1_FETCH if half == "fetch" else L1_EVICT
    edges = _l1_edges()
    _check_split_covers(edges)
    edges = [e for e in edges if e[0] in keep and e[1] in keep]
    out = ["stateDiagram-v2", "    direction LR", "    [*] --> I"]
    for src, dst, lab in sorted(_merge(edges)):
        out.append(f"    {src} --> {dst} : {lab}")
    out.append("")
    out.append("    classDef stable fill:#e8f3ea,stroke:#5a9e68,"
               "stroke-width:2px")
    out.append("    classDef wdata fill:#e4ecf7,stroke:#5b86c4")
    out.append("    classDef wack fill:#fdf2d8,stroke:#d9a441")
    out.append("    classDef wput fill:#f0e9f6,stroke:#8d6cae")
    out.append("    class " + ",".join(L1_STABLE) + " stable")
    for cls, group in zip(("wdata", "wack", "wput"),
                          L1_TRANSIENT_FILL.values()):
        group = [g for g in group if g in keep]
        if group:
            out.append("    class " + ",".join(group) + f" {cls}")
    return "\n".join(out)


def dir_mermaid(enable_e=True):
    tbl = T.dir_table(enable_e)
    edges = []
    for (st, ev), (nxt, act) in sorted(tbl.items()):
        if nxt == st:
            continue
        edges.append((T.DIR_STATE_NAMES[st], T.DIR_STATE_NAMES[nxt],
                      T.DIR_EVENT_NAMES[ev]))
    out = ["stateDiagram-v2", "    direction LR", "    [*] --> I"]
    for src, dst, lab in sorted(_merge(edges)):
        out.append(f"    {src} --> {dst} : {lab}")
    out.append("")
    out.append("    classDef stable fill:#e8f3ea,stroke:#5a9e68,"
               "stroke-width:2px")
    out.append("    classDef transient fill:#fbe6e6,stroke:#c2565c")
    out.append("    class I,S,E,M stable")
    out.append("    class S_D transient")
    return "\n".join(out)


def l1_stall_table():
    """Which events each transient state absorbs without moving."""
    rows = []
    for si, name in enumerate(T.STATE_NAMES):
        if name in L1_STABLE:
            continue
        stalls = [T.EVENT_NAMES[ev] for (st, ev), (nxt, act)
                  in sorted(T.L1_TABLE.items())
                  if st == si and "stall" in act]
        legal = [T.EVENT_NAMES[ev] for (st, ev) in sorted(T.L1_TABLE)
                 if st == si]
        illegal = [e for e in T.EVENT_NAMES if e not in legal]
        rows.append((name, stalls, illegal))
    out = ["| transient state | stalls on | no arc at all |",
           "| --- | --- | --- |"]
    for name, stalls, illegal in rows:
        out.append(f"| `{name}` | {', '.join(stalls) or '—'} | "
                   f"{', '.join(illegal) or '—'} |")
    return "\n".join(out)


BLOCKS = {
    "l1-fsm-fetch": lambda: "```mermaid\n" + l1_mermaid("fetch") + "\n```",
    "l1-fsm-evict": lambda: "```mermaid\n" + l1_mermaid("evict") + "\n```",
    "dir-fsm": lambda: "```mermaid\n" + dir_mermaid() + "\n```",
    "l1-stalls": l1_stall_table,
}


def inject(path):
    with open(path) as f:
        text = f.read()
    for key, gen in BLOCKS.items():
        begin, end = f"<!-- BEGIN {key} -->", f"<!-- END {key} -->"
        pat = re.compile(re.escape(begin) + r".*?" + re.escape(end),
                         re.DOTALL)
        if not pat.search(text):
            raise SystemExit(f"{path}: no markers for {key}")
        text = pat.sub(begin + "\n" + gen() + "\n" + end, text)
    with open(path, "w") as f:
        f.write(text)
    return path


if __name__ == "__main__":
    print(inject(DOC))
