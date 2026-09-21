"""Read the design's parameters out of coh_pkg.sv, so figures cannot invent them.

A block diagram that says "64 sets x 2 ways" is making a claim about the RTL.
Typing that claim into the drawing is how a figure starts lying -- quietly, and
usually about the number a reader most wants to trust. So the figures do not
type it: they ask here, and here parses `rtl/pkg/coh_pkg.sv`.

What this handles is deliberately small: `localparam int unsigned NAME = EXPR;`
with `$clog2` and ordinary arithmetic, and the two packed structs the packet
figure needs. That is enough for every number that appears in a diagram, and
anything more would be a SystemVerilog front end, which this project already
has two of.

If a parameter this file needs disappears from the package, `make diagrams`
fails rather than drawing a stale number.
"""
import math
import os
import re

PKG = os.path.join(os.path.dirname(__file__), "..", "..", "rtl", "pkg",
                   "coh_pkg.sv")


_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
          "eight", "nine", "ten", "eleven", "twelve")


def spell(n):
    """Small numbers read better as words in prose, and still come from here."""
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def _clog2(n):
    return int(math.ceil(math.log2(n))) if n > 1 else 1


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


class Params(dict):
    """The package's integer localparams, plus the struct field widths."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(
                f"coh_pkg.sv has no localparam {name} -- a figure is asking "
                f"for a number the design no longer has") from None

    def bits(self, name, width):
        """Format a field as 'name width', the way the packet figure wants."""
        return (name, self[width] if isinstance(width, str) else width)


def load(path=PKG):
    src = _strip_comments(open(path).read())
    p = Params()
    env = {"clog2": _clog2}

    for m in re.finditer(
            r"localparam\s+int\s+unsigned\s+(\w+)\s*=\s*([^;]+);", src):
        name, expr = m.group(1), m.group(2).strip()
        expr = re.sub(r"\$clog2", "clog2", expr)
        try:
            p[name] = int(eval(expr, {"__builtins__": {}}, env))  # noqa: S307
            env[name] = p[name]
        except Exception:
            continue  # $bits() of a struct, and anything else non-integer

    # Enum widths, for the struct fields that are enums rather than vectors.
    enum_w = {}
    for m in re.finditer(
            r"typedef\s+enum\s+logic\s*\[([^\]]+)\]\s*\{(.*?)\}\s*(\w+);",
            src, re.S):
        hi = m.group(1).split(":")[0].strip()
        hi = re.sub(r"\$clog2", "clog2", hi)
        try:
            enum_w[m.group(3)] = int(eval(hi, {"__builtins__": {}}, env)) + 1
        except Exception:
            pass

    p["_struct"] = {}
    for m in re.finditer(
            r"typedef\s+struct\s+packed\s*\{(.*?)\}\s*(\w+);", src, re.S):
        fields = []
        for line in m.group(1).split(";"):
            line = line.strip()
            if not line:
                continue
            vec = re.match(r"logic\s+(?:signed\s+)?\[([^\]]+)\]\s*(\w+)", line)
            if vec:
                hi = re.sub(r"\$clog2", "clog2", vec.group(1).split(":")[0])
                fields.append((vec.group(2),
                               int(eval(hi, {"__builtins__": {}}, env)) + 1))
                continue
            bit = re.match(r"logic\s+(\w+)$", line)
            if bit:
                fields.append((bit.group(1), 1))
                continue
            enum = re.match(r"(\w+)\s+(\w+)$", line)
            if enum and enum.group(1) in enum_w:
                fields.append((enum.group(2), enum_w[enum.group(1)]))
        p["_struct"][m.group(2)] = fields

    return p


def struct(p, name):
    """The packed fields of one struct, as [(field, width), ...]."""
    try:
        return p["_struct"][name]
    except KeyError:
        raise SystemExit(f"coh_pkg.sv has no struct {name}")


def fsm_state_count(module_path, enum_name):
    """How many states one controller FSM has, counted in its own enum.

    The directory's `12 controller states` is the kind of number that is right
    when written and wrong two phases later, so the figure counts them.
    """
    src = _strip_comments(open(module_path).read())
    m = re.search(r"typedef\s+enum\s+logic[^{]*\{(.*?)\}\s*" + enum_name
                  + r"\s*;", src, re.S)
    if not m:
        raise SystemExit(f"{module_path}: no enum {enum_name}")
    return len([e for e in m.group(1).split(",") if e.strip()])


if __name__ == "__main__":
    p = load()
    for k in ("NUM_TILES", "L1_SETS", "L1_WAYS", "L1_TAG_W", "L2_SETS",
              "L2_WAYS", "MSHR_ENTRIES", "TBE_ENTRIES", "NUM_PORTS",
              "VCS_PER_PORT", "VC_DEPTH", "LINE_BYTES", "FLIT_PAYLOAD_W"):
        print(f"{k:16} {p[k]}")
    for name in ("flit_t", "head_payload_t"):
        f = struct(p, name)
        print(f"{name}: {sum(w for _, w in f)} bits  {f}")
