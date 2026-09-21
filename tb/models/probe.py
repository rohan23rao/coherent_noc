"""Arc probe: what (state, event) pairs the protocol tables were actually shown.

A race test that only checks the final value proves the machine did not lose
data. It does *not* prove the race happened. Both are needed: without the
second, a test that never reaches the interleaving passes forever, and the
mutation check is the only thing that would notice -- much too late and much
too indirectly.

So every race test asserts a **witness**: the exact table cell the race is
about was presented to the table during the run. The probe collects those by
watching each controller's decision point:

  L1, VN1 forwards   `vn1_state` / `vn1_event` while the forward is PRESENTED,
                     not while it is accepted. A cell whose action is `stall`
                     is never accepted, and those cells are half the catalogue
                     -- IM_AD + Fwd-GetM in R6 is one. Presented is also what
                     the RTL's own legality assertion samples, so the probe and
                     the assertion agree on what counts as an event.
                     Read from the controller rather than from the debug bus
                     because an evicting line's state lives in its MSHR, not in
                     the tag array -- and MI_A / II_A are exactly the states
                     races R3, R4 and R8 are about. A message that stalls for
                     many cycles presents the same pair every cycle, so
                     consecutive repeats are collapsed: a count is a number of
                     distinct presentations, not of cycles.
  L1, VN2 responses  `vn2_event` with the state the response FSM is indexed
                     with, `state_q[vn2_set][vn2_way]`. VN2 is a sink, so the
                     valid alone is the handshake.
  L1, core requests  `s1_state` / `s1_event` while a request occupies S1, and
                     `victim_state` with Evict while S1 is choosing a victim.
                     These are the left three columns of the table and they
                     are reached only from the core side, so a probe that
                     watched the network alone would report two thirds of the
                     cache table as dead.
  Directory          `old_meta_q.dir_state` / `dir_event` while the controller
                     is in D_EXEC, which is where the table is consulted.

This is a read-only observer over signals that already exist. It adds nothing
to rtl/ and it cannot change any timing, which matters: a probe that
backpressured anything would be a timing hack by another name.
"""

from collections import Counter

from models.coherence_checker import STATE_NAMES
from tbutil import s as signed, u

NUM_TILES = 4

# coh_pkg::dir_state_e
DIR_I, DIR_S, DIR_E, DIR_M, DIR_S_D = range(5)
DIR_STATE_NAMES = ["I", "S", "E", "M", "S_D"]

# coh_pkg::l1_event_e
(EV_LOAD, EV_STORE, EV_EVICT, EV_FWD_GETS, EV_FWD_GETM, EV_INV, EV_PUT_ACK,
 EV_DATA_E_DIR, EV_DATA_DIR_A0, EV_DATA_DIR_AGT0, EV_DATA_OWNER,
 EV_INV_ACK) = range(12)
L1_EVENT_NAMES = ["Load", "Store", "Evict", "Fwd-GetS", "Fwd-GetM", "Inv",
                  "Put-Ack", "DataE", "Data(ack=0)", "Data(ack>0)",
                  "Data-owner", "Inv-Ack"]

# coh_pkg::dir_event_e
(DEV_GETS, DEV_GETM, DEV_PUTS_NOT_LAST, DEV_PUTS_LAST, DEV_PUTM_OWNER,
 DEV_PUTM_NON_OWNER, DEV_PUTE_OWNER, DEV_PUTE_NON_OWNER, DEV_DATA) = range(9)
DIR_EVENT_NAMES = ["GetS", "GetM", "PutS(not last)", "PutS(last)",
                   "PutM(owner)", "PutM(non-owner)", "PutE(owner)",
                   "PutE(non-owner)", "Data"]

# coh_pkg::msg_type_e, the one the probe names
MSG_RECALL_ACK = 22
MSG_RECALL, MSG_RECALL_INV = 12, 13
_RECALL_TYPES = (MSG_RECALL, MSG_RECALL_INV)

D_EXEC = 4          # dir_ctrl's FSM encoding for "consult the table"
DIR_STATE_LSB = 7   # dir_meta_t = valid | tag | dir_state | sharers | owner | data_valid


def l1_of(dut, tile: int):
    node = dut.gen_tile[tile]
    return node.u_tile.u_l1 if hasattr(node, "u_tile") else node.u_l1


def dir_of(dut, tile: int):
    node = dut.gen_tile[tile]
    return node.u_tile.u_dir if hasattr(node, "u_tile") else node.u_dir


def l1_arc_name(arc) -> str:
    _, state, event = arc
    return f"{STATE_NAMES[state]} + {L1_EVENT_NAMES[event]}"


def dir_arc_name(arc) -> str:
    _, state, event = arc
    return f"dir {DIR_STATE_NAMES[state]} + {DIR_EVENT_NAMES[event]}"


class ArcProbe:
    def __init__(self, dut, verbose: bool = False):
        self.dut = dut
        self.verbose = verbose
        self.l1 = [l1_of(dut, t) for t in range(NUM_TILES)]
        self.dir = [dir_of(dut, t) for t in range(NUM_TILES)]
        self.l1_arcs = Counter()   # (tile, l1_state, l1_event) -> times seen
        self.dir_arcs = Counter()  # (tile, dir_state, dir_event) -> times seen
        self.trace = []           # ordered, for failure messages
        self.min_ack = [0] * NUM_TILES
        self.samples = 0
        self._last_dir = [None] * NUM_TILES
        self._last_vn1 = [None] * NUM_TILES
        self._last_s1 = [None] * NUM_TILES
        self._last_ev = [None] * NUM_TILES
        self.sharers = Counter()
        self.recalls = 0

    @staticmethod
    def _msg_type(d) -> int:
        """msg_type is the top field of coh_msg_t; take the width from the
        handle rather than hard-coding it, so a field added to the message
        cannot silently shift what this reads."""
        return u(d.cur_q) >> (len(d.cur_q) - 5)

    def sample(self, cycle: int | None = None):
        self.samples += 1
        for t in range(NUM_TILES):
            l1 = self.l1[t]
            if u(l1.vn1_valid_i):
                if (u(l1.vn1_msg_i) >> (len(l1.vn1_msg_i) - 5)) in _RECALL_TYPES:
                    self.recalls += 1
                arc = (t, u(l1.vn1_state), u(l1.vn1_event))
                if self._last_vn1[t] != arc:
                    self.l1_arcs[arc] += 1
                    taken = " (stalled)" if not u(l1.vn1_ready_o) else ""
                    self._note(cycle, f"tile {t} L1 {l1_arc_name(arc)}{taken}")
                self._last_vn1[t] = arc
            else:
                self._last_vn1[t] = None
            if u(l1.vn2_valid_i):
                st = u(l1.state_q[u(l1.vn2_set)][u(l1.vn2_way)])
                arc = (t, st, u(l1.vn2_event))
                self.l1_arcs[arc] += 1
                self._note(cycle, f"tile {t} L1 {l1_arc_name(arc)}")
                self.min_ack[t] = min(self.min_ack[t],
                                      signed(l1.vn2_ack_next))

            if u(l1.s1_valid_q):
                arc = (t, u(l1.s1_state), u(l1.s1_event))
                if self._last_s1[t] != arc:
                    self.l1_arcs[arc] += 1
                    self._note(cycle, f"tile {t} L1 {l1_arc_name(arc)}")
                self._last_s1[t] = arc
                if u(l1.needs_evict):
                    varc = (t, u(l1.victim_state), EV_EVICT)
                    if self._last_ev[t] != varc:
                        self.l1_arcs[varc] += 1
                        self._note(cycle, f"tile {t} L1 {l1_arc_name(varc)}")
                    self._last_ev[t] = varc
                else:
                    self._last_ev[t] = None
            else:
                self._last_s1[t] = None
                self._last_ev[t] = None

            d = self.dir[t]
            if u(d.fsm_q) == D_EXEC:
                self.sharers[bin((u(d.old_meta_q) >> 3) & 0xF).count("1")] += 1
                # A recall response is handled outside the table, so the
                # table's event decode for it is meaningless -- see D18.
                if self._msg_type(d) == MSG_RECALL_ACK:
                    continue
                arc = (t, (u(d.old_meta_q) >> DIR_STATE_LSB) & 0x7,
                       u(d.dir_event))
                if self._last_dir[t] != arc:
                    self.dir_arcs[arc] += 1
                    self._note(cycle, f"tile {t} {dir_arc_name(arc)}")
                self._last_dir[t] = arc
            else:
                self._last_dir[t] = None

    def _note(self, cycle, what: str):
        self.trace.append((cycle, what))
        if self.verbose:
            self.dut._log.info("arc %6s  %s", cycle, what)

    # -- witnesses -----------------------------------------------------------
    def saw_l1(self, tile: int, state: int, event: int) -> bool:
        return (tile, state, event) in self.l1_arcs

    def saw_dir(self, tile: int, state: int, event: int) -> bool:
        return (tile, state, event) in self.dir_arcs

    def require_l1(self, tile: int, state: int, event: int, why: str):
        assert self.saw_l1(tile, state, event), (
            f"the interleaving never happened: tile {tile}'s L1 was never shown "
            f"{STATE_NAMES[state]} + {L1_EVENT_NAMES[event]}. {why}\n"
            f"{self.summary()}"
        )

    def require_dir(self, tile: int, state: int, event: int, why: str):
        assert self.saw_dir(tile, state, event), (
            f"the interleaving never happened: bank {tile} was never shown "
            f"{DIR_STATE_NAMES[state]} + {DIR_EVENT_NAMES[event]}. {why}\n"
            f"{self.summary()}"
        )

    def count_dir(self, tile: int, state: int, event: int) -> int:
        return self.dir_arcs[(tile, state, event)]

    def count_l1(self, tile: int, state: int, event: int) -> int:
        return self.l1_arcs[(tile, state, event)]

    def l1_events_in(self, tile: int, state: int) -> set:
        """Every event the given tile was shown while in `state`."""
        return {ev for (t, st, ev) in self.l1_arcs if t == tile and st == state}

    def dir_states_for(self, tile: int, event: int) -> set:
        """Every directory state in which `event` was seen at this bank."""
        return {st for (t, st, ev) in self.dir_arcs if t == tile and ev == event}

    def require_dir_event(self, tile: int, event: int, why: str):
        """The event reached the bank, whatever state it found it in.

        Used where the race is about the event arriving late rather than about
        one particular cell -- R5's stale PutE can legitimately land in E or in
        M depending on how far the new owner has got.
        """
        assert self.dir_states_for(tile, event), (
            f"the interleaving never happened: bank {tile} was never shown "
            f"{DIR_EVENT_NAMES[event]} in any state. {why}\n{self.summary()}")

    def summary(self) -> str:
        lines = [f"  {'' if c is None else c:>6} {what}"
                 for c, what in self.trace[-40:]]
        head = f"last {len(lines)} of {len(self.trace)} observed arcs " \
               f"({self.samples} cycles sampled):"
        return "\n".join([head] + lines)


class LivenessGauge:
    """Worst observed MSHR and TBE age, against the bounds that assert on them.

    The liveness assertions are the deadlock detector, and a bound is only
    meaningful if somebody has measured how close normal operation gets to it.
    A bound that fires at ten times the worst real latency is a detector; one
    that fires at 1.2x is a flake generator. Reporting both numbers is what
    turns "the assertion did not fire" into "the assertion did not fire, and
    here is the margin".

    Sampled every `every` cycles rather than continuously: an age counter
    increments monotonically while an entry is live, so sampling can miss the
    peak by at most `every` cycles, and that error is one-sided.
    """

    def __init__(self, dut, every: int = 16, mshr_bound: int = 1000,
                 tbe_bound: int = 500):
        self.dut = dut
        self.every = every
        self.mshr_bound = mshr_bound
        self.tbe_bound = tbe_bound
        self.mshr_ages = [l1_of(dut, t).u_mshr.age_q for t in range(NUM_TILES)]
        self.tbe_ages = [dir_of(dut, t).u_tbe.age_q for t in range(NUM_TILES)]
        self.worst_mshr = 0
        self.worst_tbe = 0
        self._n = 0

    def sample(self):
        self._n += 1
        if self._n % self.every:
            return
        for ages in self.mshr_ages:
            for i in range(4):
                self.worst_mshr = max(self.worst_mshr, u(ages[i]))
        for ages in self.tbe_ages:
            for i in range(4):
                self.worst_tbe = max(self.worst_tbe, u(ages[i]))

    def summary(self) -> str:
        return (f"worst MSHR age {self.worst_mshr} of a {self.mshr_bound}-cycle "
                f"bound ({self.mshr_bound / max(self.worst_mshr, 1):.0f}x "
                f"margin); worst TBE age {self.worst_tbe} of {self.tbe_bound} "
                f"({self.tbe_bound / max(self.worst_tbe, 1):.0f}x)")
