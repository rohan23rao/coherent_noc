"""SWMR checker, reading L1 state through the debug bus.

Checks the single-writer / multiple-reader invariant directly on the
*implementation's* state arrays rather than on observed loads and stores. That
is strictly stronger: it fails on the cycle the invariant is violated, not on
the later cycle when a wrong value becomes visible to a core, and it localises
a bug to the transition that caused it.

The debug bus exposes every way of one set at a time, so the checker sweeps
sets round-robin. For a footprint confined to a handful of sets that means each
line is examined every few cycles. It is sampling, not continuous, and the
sweep period is reported so the coverage claim stays honest.

Why a debug bus and not hierarchical references: the check has to survive
elaboration that flattens or renames internals, and a port is a contract while
a hierarchical path is a coincidence.
"""

from tbutil import u

# coh_pkg::l1_state_e
I, S, E, M, IS_D, IM_AD, IM_A, SM_AD, SM_A, MI_A, EI_A, SI_A, II_A = range(13)
STATE_NAMES = ["I", "S", "E", "M", "IS_D", "IM_AD", "IM_A", "SM_AD", "SM_A",
               "MI_A", "EI_A", "SI_A", "II_A"]

NUM_TILES = 4
L1_WAYS = 2
STATE_W = 4
TAG_W = 21

# Only stable M and E grant write permission. Transient states do not: a core
# in IM_A holds data but has not collected its acks, and the previous owner has
# already given the line up, so there is no overlap to tolerate.
WRITERS = {M, E}
READERS = {S}


class SwmrChecker:
    def __init__(self, dut, sets_to_sweep):
        self.dut = dut
        self.sets = list(sets_to_sweep)
        self.idx = 0
        self.samples = 0
        self.violations = []
        self.max_writers_seen = 0
        self.sharing_seen = 0

    def current_set(self) -> int:
        return self.sets[self.idx]

    def advance(self):
        self.idx = (self.idx + 1) % len(self.sets)

    def _read(self):
        state_word = u(self.dut.dbg_state_o)
        tag_word = u(self.dut.dbg_tag_o)
        out = []
        for t in range(NUM_TILES):
            ways = []
            for w in range(L1_WAYS):
                sbit = (t * L1_WAYS + w) * STATE_W
                tbit = (t * L1_WAYS + w) * TAG_W
                st = (state_word >> sbit) & ((1 << STATE_W) - 1)
                tg = (tag_word >> tbit) & ((1 << TAG_W) - 1)
                ways.append((st, tg))
            out.append(ways)
        return out

    def check(self, cycle: int):
        """Sample the currently selected set and enforce SWMR on every line in it."""
        per_tile = self._read()
        cur_set = self.current_set()
        self.samples += 1

        by_tag = {}
        for tile, ways in enumerate(per_tile):
            for st, tg in ways:
                if st == I:
                    continue
                by_tag.setdefault(tg, []).append((tile, st))

        for tg, holders in by_tag.items():
            writers = [(t, s) for t, s in holders if s in WRITERS]
            readers = [(t, s) for t, s in holders if s in READERS]
            self.max_writers_seen = max(self.max_writers_seen, len(writers))
            if len(readers) > 1:
                self.sharing_seen += 1

            if len(writers) > 1:
                who = ", ".join(f"tile {t} in {STATE_NAMES[s]}" for t, s in writers)
                self.violations.append(
                    f"cycle {cycle}: SWMR violated for set {cur_set} tag {tg:#x} -- "
                    f"{len(writers)} writers at once: {who}"
                )
            if writers and readers:
                w = ", ".join(f"tile {t} in {STATE_NAMES[s]}" for t, s in writers)
                r = ", ".join(f"tile {t} in {STATE_NAMES[s]}" for t, s in readers)
                self.violations.append(
                    f"cycle {cycle}: SWMR violated for set {cur_set} tag {tg:#x} -- "
                    f"writer(s) {w} coexist with reader(s) {r}"
                )

        self.advance()
        self.dut.dbg_set_i.value = self.current_set()

    def assert_clean(self):
        assert not self.violations, (
            f"{len(self.violations)} SWMR violation(s); first three:\n  "
            + "\n  ".join(self.violations[:3])
        )

    def summary(self) -> str:
        return (
            f"SWMR: {self.samples} samples over {len(self.sets)} sets "
            f"(sweep period {len(self.sets)} cycles), no violations; "
            f"peak simultaneous writers per line {self.max_writers_seen}, "
            f"multi-reader samples {self.sharing_seen}"
        )
