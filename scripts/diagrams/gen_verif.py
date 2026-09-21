"""The verification diagram: six tiers, what each one cannot prove, and the
loop that checks the tests themselves."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from svg import Svg, VNET, INK, MUTED, RULE, ROLE
import params

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
OUT = os.path.join(ROOT, "docs", "img")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tb"))
import mutations as MUT  # noqa: E402
from models import coverage as COV  # noqa: E402

# The three numbers this figure would otherwise get wrong first: how many
# mutations there are, and how long the two coverage lists are. They come from
# the files that own them.
N_MUTATIONS = len(MUT.MUTATIONS)
N_DEFENSIVE = len(COV.DEFENSIVE)
N_DEFENSIVE_BINS = len(COV.DEFENSIVE_BINS)

RED, AMBER, BLUE, GREEN = "#c2565c", "#d9a441", "#3f7fb5", "#5a9e68"


def verification(path):
    s = Svg(1240, 1000, "Verification — six tiers, and what each one cannot do",
            "Every tier has a column for what it proves and a column for what "
            "it cannot. The second column is the one that decided what the "
            "next tier had to be.")

    tiers = [
        ("1", "Unit", "test_unit_*.py", GREEN,
         "each building block does what its header says — including the"
         " arbiter's fairness property, measured over 10,000 cycles",
         "anything about how they are composed"),
        ("2", "Network standalone", "test_noc_router / _mesh", GREEN,
         "every packet ejected exactly once, at the right node, flits in"
         " order, vnet unchanged; credit and buffer assertions live",
         "anything about message CONTENT. A network that delivers every"
         " packet correctly can still reorder two — bug B19, and this tier"
         " passed throughout"),
        ("3", "Protocol tables", "test_protocol_*_table.py", BLUE,
         "the RTL tables match an independent Python transcription cell by"
         " cell, blanks included — no permissive default anywhere",
         "that the table is the RIGHT table. Two transcriptions of the same"
         " mistake would agree"),
        ("4", "Directed protocol", "test_races.py + 4 more", BLUE,
         "the twelve named interleavings happen and are handled. Each test"
         " asserts a WITNESS — the exact cell it is named after was"
         " presented — before it asserts any value",
         "that the catalogue is complete. It is a list of the races somebody"
         " thought of"),
        ("5", "Constrained random", "test_stress.py, 5 configs", AMBER,
         "100,000 requests per footprint with no assertion failure, no SWMR"
         " violation, no value mismatch. Found B16, B19, B20 (x3) and B21",
         "absence. The parts of the state space it does not reach are"
         " exactly what the coverage report is for"),
        ("6", "Mutation", f"make mutate, {N_MUTATIONS} mutations", RED,
         "the directed tier is sensitive to the arcs it claims to cover."
         " A mutation that survives is an open item, not a pass",
         "that the arcs NOT in the table are unnecessary"),
    ]

    X0, W, H, GAP = 110, 1070, 86, 12
    for i, (n, name, files, col, proves, cannot) in enumerate(tiers):
        y = 100 + i * (H + GAP)
        s.rect(X0, y, W, H, fill="#ffffff", stroke=col, rx=8, sw=1.6)
        s.rect(X0, y, 8, H, fill=col, stroke=col, rx=3)
        s.text(X0 + 34, y + 36, n, size=20, weight="700", fill=col)
        s.text(X0 + 56, y + 28, name, size=13, anchor="start", weight="700",
               fill=col)
        s.text(X0 + 56, y + 46, files, size=9.5, anchor="start", fill=MUTED)
        s.text(X0 + 300, y + 18, "proves", size=9, anchor="start",
               fill=GREEN, weight="700")
        _wrap(s, X0 + 300, y + 34, 420, proves, INK)
        s.text(X0 + 760, y + 18, "cannot prove", size=9, anchor="start",
               fill=RED, weight="700")
        _wrap(s, X0 + 760, y + 34, 290, cannot, MUTED)
        if i:
            s.arrow([(X0 + 250, y - GAP), (X0 + 250, y)], color=MUTED, sw=1.6)

    # The loop: mutation is the only tier whose subject is another tier.
    y6 = 100 + 5 * (H + GAP)
    y4 = 100 + 3 * (H + GAP)
    s.arrow([(X0, y6 + H / 2), (62, y6 + H / 2), (62, y4 + H / 2),
             (X0, y4 + H / 2)], color=RED, sw=2.0)
    s.raw(f'<g transform="translate(46,{(y4 + y6) / 2 + H / 2}) rotate(-90)">')
    s.text(0, 0, "mutation's subject is the tests, not the design", size=10,
           fill=RED, mono=False)
    s.raw("</g>")

    yb = 100 + 6 * (H + GAP) + 16
    s.rect(110, yb, 525, 232, fill="#f7f9fb", stroke=RULE, rx=8)
    s.text(134, yb + 26, "Three instruments, each built for one failure",
           size=12.5, anchor="start", weight="650")
    s.note(134, yb + 48, [
        "ArcProbe — records every (state, event) PRESENTED to a",
        "controller, not every one accepted. A test that checks only",
        "outcomes cannot tell 'handled correctly' from 'never happened',",
        "and R5 passed for three phases without ever producing the",
        "message in its name (bug B17).",
        "",
        "Hang dump — on a liveness timeout, prints every MSHR, every",
        "TBE and the last 200 messages. Turned a 3000-cycle opaque",
        "stall into a readable sequence.",
        "",
        "LivenessGauge — reports the worst age observed against the",
        "bound, so a passing run says how much margin it had.",
    ], size=9.8)

    s.rect(655, yb, 525, 232, fill="#f7f9fb", stroke=RULE, rx=8)
    s.text(679, yb + 26, "Coverage, kept as two lists rather than one number",
           size=12.5, anchor="start", weight="650")
    s.note(679, yb + 48, [
        "UNCOVERED AND REACHABLE — must be empty, and is.",
        "",
        f"UNCOVERED BY CONSTRUCTION — {N_DEFENSIVE} table cells and",
        f"{params.spell(N_DEFENSIVE_BINS)} occupancy bin, each with a "
        f"written argument for why the",
        "design cannot reach it. Not a waiver list: a waiver says",
        "'ignore this', and each of these says why the cell is",
        "unreachable, which is a claim about the implementation",
        "that can be wrong and can be checked.",
        "",
        "A single coverage percentage would have hidden both lists,",
        "and the second one is where the design's real invariants",
        "are written down.",
    ], size=9.8)

    return s.write(path)


def _wrap(s, x, y, width, text, fill, size=10, gap=13):
    """Greedy wrap at an approximate character width for the sans face."""
    per = max(8, int(width / (size * 0.56)))
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > per:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    s.lines(x, y, out, size=size, anchor="start", fill=fill, mono=False,
            gap=gap)


if __name__ == "__main__":
    print(verification(os.path.join(OUT, "verification.svg")))
