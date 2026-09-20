"""Functional coverage: what the stress run actually exercised.

A stress run that passes tells you nothing about what it *reached*. This
collects the bins the specification asks for and, at the end, prints the ones
that are still empty. An empty uncovered list is the claim; a non-empty one is
an open item with a name attached, not a pass with a caveat.

Five groups:

  l1_arc    cross of (L1 state x event). The legal bins are the keys of
            `models.tables.L1_TABLE` -- the same transcription the table test
            checks the RTL against, so "legal" means one thing in this project.
  dir_arc   cross of (directory state x event), likewise.
  mshr_occ  MSHR occupancy 0..4 per cache.
  vc_occ    virtual-channel buffer occupancy 0..VC_DEPTH.
  sharers   number of sharers recorded for a line, 0..4.
  race      one bin per named race, defined as the condition that race needs.

Two honest caveats, both of which the report prints rather than hides.

**Some bins are sampled, not continuous.** The arc bins are sampled every
cycle, because an accepted event lasts exactly one cycle and anything less
would miss them. Occupancy is sampled every `occ_every` cycles: an occupancy
of four persists while the buffer stays full, so the sampling error is
one-sided and the report states the period.

**Some legal cells are unreachable.** A table can define a cell that no
sequence of messages can produce -- the cell exists because leaving it blank
would make a defensive assertion fire on a case the designer has not thought
through, not because the case happens. Those are listed in `DEFENSIVE` with an
argument each, and the report separates "uncovered and reachable" (an open
item) from "uncovered by construction" (a claim that has to be defended).
"""

from collections import Counter

from models.probe import (ArcProbe, DIR_EVENT_NAMES, DIR_STATE_NAMES,
                          L1_EVENT_NAMES, dir_of, l1_of)
from models.coherence_checker import STATE_NAMES
from models.tables import L1_TABLE, dir_table
from tbutil import u

NUM_TILES = 4
NUM_PORTS = 5
VCS_PER_PORT = 6
VC_DEPTH = 4
MSHR_ENTRIES = 4

# coh_pkg::l1_state_e
(L1_I, L1_S, L1_E, L1_M, L1_IS_D, L1_IM_AD, L1_IM_A, L1_SM_AD, L1_SM_A,
 L1_MI_A, L1_EI_A, L1_SI_A, L1_II_A) = range(13)

# coh_pkg::l1_event_e
(EV_LOAD, EV_STORE, EV_EVICT, EV_FWD_GETS, EV_FWD_GETM, EV_INV, EV_PUT_ACK,
 EV_DATA_E_DIR, EV_DATA_DIR_A0, EV_DATA_DIR_AGT0, EV_DATA_OWNER,
 EV_INV_ACK) = range(12)

# coh_pkg::dir_state_e / dir_event_e
DIR_I, DIR_S, DIR_E, DIR_M, DIR_S_D = range(5)
(DEV_GETS, DEV_GETM, DEV_PUTS_NOT_LAST, DEV_PUTS_LAST, DEV_PUTM_OWNER,
 DEV_PUTM_NON_OWNER, DEV_PUTE_OWNER, DEV_PUTE_NON_OWNER, DEV_DATA) = range(9)


# ---------------------------------------------------------------------------
# Legal cells that no message sequence can produce, each with its argument.
#
# A blank cell in these tables raises `illegal` and stops the simulation, so a
# cell is filled in whenever the designer is not certain the case is
# impossible. Some of those turn out, on inspection of the microarchitecture
# rather than of the protocol, to be unreachable: the table is written against
# the protocol, and the implementation forecloses the case earlier. Those are
# not coverage holes. They are claims, and each is written out so it can be
# argued with -- which is the point of reporting them separately instead of
# quietly excluding them.
# ---------------------------------------------------------------------------
_INV_NOT_A_SHARER = (
    "A cache reaches IM_AD either from I -- it was never a sharer -- or from "
    "SM_AD by taking an Inv, which is what removed it from the sharer vector. "
    "IM_A follows IM_AD. Either way the directory does not list it, and Inv "
    "goes only to sharers. The cell is a stall so that a protocol change which "
    "stopped clearing the sharer vector on GetM would degrade to a retry "
    "rather than to an assertion failure.")

_NEVER_A_VICTIM = (
    "A line with a fetch outstanding occupies the way its own MSHR reserved, "
    "and reserved ways are excluded from victim selection (`set_reserved` in "
    "l1_cache). The replacement policy therefore never offers such a line, so "
    "the Evict event is not merely handled here -- it is never generated.")

_EVICT_STATE_HIDDEN = (
    "An evicting line's state lives in its MSHR and its way in the array has "
    "already been invalidated, so a core request for that line misses and the "
    "table is consulted with I rather than with the eviction transient. The "
    "request is then blocked by the MSHR address check and replays. The cell "
    "says what the protocol would require if the eviction state were visible "
    "to the core pipeline; in this implementation it is not.")

_NO_FORWARD_TO_AN_UPGRADER = (
    "Data-owner is sent by a previous owner in answer to a forward. A cache in "
    "SM_AD holds a shared copy, so the directory is in S or S_D for that line "
    "and serves its GetM itself with Data+AckCount -- it has no owner to "
    "forward to. If the cache is invalidated while upgrading it moves to "
    "IM_AD, and it is there that a forwarded copy can arrive.")

_EMPTY_VECTOR_IS_ALWAYS_LAST = (
    "PutS is split by `is_last_sharer`, computed as "
    "`(sharers & ~requester) == 0`. In I, E and M the sharer vector is empty, "
    "so that expression is always true and only the `last` variant of the "
    "event can be generated. The `not last` cell is reachable in S and S_D, "
    "where it is covered.")

_AN_OWNER_IN_M_SENDS_PUTM = (
    "The directory reaches M only by serving a GetM, which sets the owner to "
    "the requester -- and that requester is in M, so its eviction sends PutM. "
    "A cache in E evicting with PutE can certainly find the directory in M, "
    "but by then the owner is somebody else and the event is the non-owner "
    "variant, which is covered. PutE from the recorded owner while in M has "
    "no producer.")


DEFENSIVE = {
    # ---- the cache ---------------------------------------------------------
    ("l1", L1_IM_AD, EV_INV): _INV_NOT_A_SHARER,
    ("l1", L1_IM_A, EV_INV): _INV_NOT_A_SHARER,

    ("l1", L1_IS_D, EV_EVICT): _NEVER_A_VICTIM,
    ("l1", L1_IM_AD, EV_EVICT): _NEVER_A_VICTIM,
    ("l1", L1_IM_A, EV_EVICT): _NEVER_A_VICTIM,
    ("l1", L1_SM_AD, EV_EVICT): _NEVER_A_VICTIM,
    ("l1", L1_SM_A, EV_EVICT): _NEVER_A_VICTIM,

    ("l1", L1_MI_A, EV_LOAD): _EVICT_STATE_HIDDEN,
    ("l1", L1_MI_A, EV_STORE): _EVICT_STATE_HIDDEN,
    ("l1", L1_MI_A, EV_EVICT): _EVICT_STATE_HIDDEN,
    ("l1", L1_EI_A, EV_LOAD): _EVICT_STATE_HIDDEN,
    ("l1", L1_EI_A, EV_STORE): _EVICT_STATE_HIDDEN,
    ("l1", L1_EI_A, EV_EVICT): _EVICT_STATE_HIDDEN,
    ("l1", L1_SI_A, EV_LOAD): _EVICT_STATE_HIDDEN,
    ("l1", L1_SI_A, EV_STORE): _EVICT_STATE_HIDDEN,
    ("l1", L1_SI_A, EV_EVICT): _EVICT_STATE_HIDDEN,
    ("l1", L1_II_A, EV_LOAD): _EVICT_STATE_HIDDEN,
    ("l1", L1_II_A, EV_STORE): _EVICT_STATE_HIDDEN,
    ("l1", L1_II_A, EV_EVICT): _EVICT_STATE_HIDDEN,

    ("l1", L1_SM_AD, EV_DATA_OWNER): _NO_FORWARD_TO_AN_UPGRADER,

    # ---- the directory -----------------------------------------------------
    ("dir", DIR_I, DEV_PUTS_NOT_LAST): _EMPTY_VECTOR_IS_ALWAYS_LAST,
    ("dir", DIR_E, DEV_PUTS_NOT_LAST): _EMPTY_VECTOR_IS_ALWAYS_LAST,
    ("dir", DIR_M, DEV_PUTS_NOT_LAST): _EMPTY_VECTOR_IS_ALWAYS_LAST,

    ("dir", DIR_M, DEV_PUTE_OWNER): _AN_OWNER_IN_M_SENDS_PUTM,
}


def _legal_l1() -> set:
    return set(L1_TABLE.keys())


def _legal_dir() -> set:
    return set(dir_table(True).keys())


class Coverage:
    """Accumulates bins across one or more runs in a single simulation."""

    def __init__(self, dut, occ_every: int = 4):
        self.dut = dut
        self.occ_every = occ_every
        self.probe = ArcProbe(dut)
        self.l1_hits = Counter()
        self.dir_hits = Counter()
        self.mshr_occ = Counter()
        self.vc_occ = Counter()
        self.sharers = Counter()
        self.race = Counter()
        self.cycles = 0
        self._n = 0

        self.mshr_valid = dut.dbg_mshr_valid_o
        self.dirs = [dir_of(dut, t) for t in range(NUM_TILES)]
        self.l1s = [l1_of(dut, t) for t in range(NUM_TILES)]
        # Local input port of each router: where a tile's own traffic enters
        # the network and where a blocked VN0 would sit on top of its VN1 and
        # VN2 (race R12). PORT_LOCAL is index 4.
        self.vc_counts = []
        for r in range(NUM_TILES):
            iu = dut.u_noc.gen_router[r].u_router.gen_input_unit[4].u_input_unit
            for v in range(VCS_PER_PORT):
                self.vc_counts.append(iu.gen_vc_buf[v].u_vc_buf.count_q)

    # -- sampling -----------------------------------------------------------
    def sample(self, cycle: int | None = None):
        self.cycles += 1
        self._n += 1
        self.probe.sample(cycle)

        # Sharer counts and recall messages come from the probe, which has
        # already read those signals this cycle. Reading them again here
        # doubles the simulator round trips for no new information, and this
        # loop runs on every cycle of a 100,000-request stress run.
        if self._n % self.occ_every == 0:
            mv = u(self.mshr_valid)
            for t in range(NUM_TILES):
                self.mshr_occ[bin((mv >> (t * MSHR_ENTRIES)) &
                                  ((1 << MSHR_ENTRIES) - 1)).count("1")] += 1
            for h in self.vc_counts:
                self.vc_occ[u(h)] += 1

    # -- races --------------------------------------------------------------
    def _fold_arcs(self):
        """Per-tile arcs -> tile-independent bins. A cell is covered when any
        cache or any bank has been shown it; which tile saw it is a property of
        the traffic pattern, not of the protocol."""
        for (_, st, ev), n in self.probe.l1_arcs.items():
            self.l1_hits[(st, ev)] += n
        for (_, st, ev), n in self.probe.dir_arcs.items():
            self.dir_hits[(st, ev)] += n

    def _fold_races(self):
        p = self.probe
        def any_l1(st, ev):
            return sum(p.count_l1(t, st, ev) for t in range(NUM_TILES))
        def any_dir(st, ev):
            return sum(p.count_dir(t, st, ev) for t in range(NUM_TILES))

        self.race["R1"] += any_l1(L1_IM_AD, EV_INV_ACK)
        self.race["R2"] += any_l1(L1_SM_AD, EV_INV)
        self.race["R3"] += any_l1(L1_MI_A, EV_FWD_GETM)
        self.race["R4"] += (any_dir(DIR_M, DEV_PUTS_LAST) +
                            any_dir(DIR_M, DEV_PUTS_NOT_LAST))
        self.race["R5"] += sum(any_dir(st, DEV_PUTE_NON_OWNER) for st in range(5))
        self.race["R6"] += any_l1(L1_IM_AD, EV_FWD_GETM)
        self.race["R7"] += any_dir(DIR_S_D, DEV_GETS)
        self.race["R8"] += any_l1(L1_II_A, EV_PUT_ACK)  # II_A was reached
        # R9 is counted during sampling, from the recall message type.
        self.race["R10"] += any_dir(DIR_M, DEV_GETM)    # the line ping-ponged
        self.race["R11"] += any_dir(DIR_E, DEV_PUTM_OWNER)
        self.race["R12"] += self.vc_occ.get(VC_DEPTH, 0)  # a VC buffer filled

    # -- reporting ----------------------------------------------------------
    def report(self) -> tuple[str, list]:
        """Returns (printable report, list of uncovered reachable bins)."""
        self._fold_arcs()
        self.sharers += self.probe.sharers
        self.race["R9"] += self.probe.recalls
        self._fold_races()
        lines = []
        open_items = []

        def group(kind, name, legal, hits, fmt):
            covered = {b for b in legal if hits.get(b)}
            missing = sorted(legal - covered)
            defensive = [b for b in missing if (kind, b[0], b[1]) in DEFENSIVE]
            reachable = [b for b in missing if b not in defensive]
            lines.append(f"{name:<10} {len(covered):>4}/{len(legal):<4} covered"
                         + (f", {len(defensive)} uncovered by construction"
                            if defensive else ""))
            for b in reachable:
                lines.append(f"             UNCOVERED  {fmt(b)}")
                open_items.append((name, fmt(b)))
            for b in defensive:
                lines.append(f"             by construction: {fmt(b)}")
                lines.append(f"               {DEFENSIVE[(kind, b[0], b[1])]}")

        lines.append(f"functional coverage over {self.cycles} sampled cycles "
                     f"(arcs every cycle, occupancy every {self.occ_every})")
        group("l1", "l1_arc", _legal_l1(), self.l1_hits, _fmt_l1)
        group("dir", "dir_arc", _legal_dir(), self.dir_hits, _fmt_dir)

        for name, legal, hits, fmt in (
            ("mshr_occ", set(range(MSHR_ENTRIES + 1)), self.mshr_occ, str),
            ("vc_occ", set(range(VC_DEPTH + 1)), self.vc_occ, str),
            ("sharers", set(range(NUM_TILES + 1)), self.sharers, str),
        ):
            covered = {b for b in legal if hits.get(b)}
            missing = sorted(legal - covered)
            lines.append(f"{name:<10} {len(covered):>4}/{len(legal):<4} covered"
                         f"   {dict(sorted(hits.items()))}")
            for b in missing:
                lines.append(f"             UNCOVERED  bin {fmt(b)}")
                open_items.append((name, str(b)))

        races = [f"R{i}" for i in range(1, 13)]
        missing = [r for r in races if not self.race.get(r)]
        lines.append(f"{'race':<10} {len(races) - len(missing):>4}/{len(races):<4} "
                     f"covered   " +
                     " ".join(f"{r}:{self.race.get(r, 0)}" for r in races))
        for r in missing:
            lines.append(f"             UNCOVERED  race {r}")
            open_items.append(("race", r))

        return "\n".join(lines), open_items


def _fmt_l1(b) -> str:
    return f"L1 {STATE_NAMES[b[0]]} + {L1_EVENT_NAMES[b[1]]}"


def _fmt_dir(b) -> str:
    return f"dir {DIR_STATE_NAMES[b[0]]} + {DIR_EVENT_NAMES[b[1]]}"
