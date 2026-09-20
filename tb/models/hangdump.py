"""What a stuck transaction is waiting for.

A liveness assertion says that something did not finish. It names the entry and
the line, which is a great deal more than a testbench timeout, and still not a
diagnosis. This walks every directory and every cache and prints the state that
answers "waiting for what": which controller is in which state, which messages
are offered and which are being accepted, and what each MSHR holds.

It is a debugging instrument, so it reads internal signals by name. It runs
only when a test asks for it.
"""

from models.coherence_checker import STATE_NAMES
from models.probe import dir_of, l1_of


def nic_of(dut, tile: int):
    return dut.gen_tile[tile].u_tile.u_nic


def router_of(dut, r: int):
    return dut.u_noc.gen_router[r].u_router


PORTS = "NESWL"
MSG_NAMES = {0: "GetS", 1: "GetM", 2: "PutS", 3: "PutM", 4: "PutE",
             8: "FwdGetS", 9: "FwdGetM", 10: "Inv", 11: "PutAck",
             12: "Recall", 13: "RecallInv", 16: "Data", 17: "DataE",
             18: "DataOwner", 19: "InvAck", 20: "WbData", 22: "RecallAck"}
VC_ST = "-RAX"   # IDLE, ROUTED, ALLOC, ACTIVE
from tbutil import u

NUM_TILES = 4
ENTRIES = 4
LINE_ADDR_W = 27
L1_STATE_W = 4
TBE_STATE_W = 2
TBE_STATE_NAMES = ["INVALID", "MEM_PEND", "INV_PEND", "WB_PEND"]
DIR_FSM_NAMES = ["IDLE", "LOOK", "MEM_REQ", "MEM_WAIT", "EXEC", "WRDATA",
                 "SEND", "BINV", "BSEND", "BWB", "BFREE", "MEM_WAIT_WB"]


def _mshr_lines(l1) -> str:
    raw = u(l1.mshr)
    w = len(l1.mshr) // ENTRIES
    out = []
    for k in range(ENTRIES):
        e = (raw >> (k * w)) & ((1 << w) - 1)
        if (e >> (w - 1)) & 1:
            a = (e >> (w - 1 - LINE_ADDR_W)) & ((1 << LINE_ADDR_W) - 1)
            st = (e >> (w - 1 - LINE_ADDR_W - L1_STATE_W)) & 0xF
            out.append(f"{k}:{a:#x}/{STATE_NAMES[st]}")
    return ", ".join(out) or "-"


def _tbe_lines(d) -> str:
    raw = u(d.u_tbe.tbe_q)
    w = len(d.u_tbe.tbe_q) // ENTRIES
    out = []
    for k in range(ENTRIES):
        e = (raw >> (k * w)) & ((1 << w) - 1)
        if (e >> (w - 1)) & 1:
            st = (e >> (w - 1 - TBE_STATE_W)) & 0x3
            a = (e >> (w - 1 - TBE_STATE_W - LINE_ADDR_W)) & ((1 << LINE_ADDR_W) - 1)
            age = u(d.u_tbe.age_q[k])
            out.append(f"{k}:{a:#x}/{TBE_STATE_NAMES[st]}/age{age}")
    return ", ".join(out) or "-"


def stuck(dut, threshold: int) -> list:
    """Names of every entry that has been live longer than `threshold`."""
    out = []
    for t in range(NUM_TILES):
        ages = l1_of(dut, t).u_mshr.age_q
        for i in range(ENTRIES):
            if u(ages[i]) >= threshold:
                out.append(f"tile {t} MSHR {i} live {u(ages[i])} cycles")
    for b in range(NUM_TILES):
        ages = dir_of(dut, b).u_tbe.age_q
        for i in range(ENTRIES):
            if u(ages[i]) >= threshold:
                out.append(f"bank {b} TBE {i} live {u(ages[i])} cycles")
    return out


def dump(dut, cycle: int, who: list) -> str:
    lines = [f"cycle {cycle}: " + "; ".join(who)]
    for b in range(NUM_TILES):
        d = dir_of(dut, b)
        fsm = u(d.fsm_q)
        lines.append(
            f"  bank {b} {DIR_FSM_NAMES[fsm] if fsm < len(DIR_FSM_NAMES) else fsm}"
            f" cur={(u(d.cur_q) >> (len(d.cur_q) - 32)) & ((1 << LINE_ADDR_W) - 1):#x}"
            f"/type{u(d.cur_q) >> (len(d.cur_q) - 5)}"
            f" inv_pend={u(d.inv_pend_q):#x}"
            f" binv_inv={u(d.binv_inv_pend_q):#x}"
            f" binv_rec={u(d.binv_recall_pend_q)}"
            f" binv_addr={u(d.binv_addr_q):#x}"
            f" vn0[v{u(d.vn0_valid_i)} r{u(d.vn0_ready_o)}]"
            f" vn1[v{u(d.vn1_valid_o)} r{u(d.vn1_ready_i)}]"
            f" vn2[v{u(d.vn2_valid_i)}]"
            f" tbe[{_tbe_lines(d)}]")
    for t in range(NUM_TILES):
        l1 = l1_of(dut, t)
        w = len(l1.vn1_msg_i)
        lines.append(
            f"  tile {t} mshr[{_mshr_lines(l1)}]"
            f" vn0[v{u(l1.vn0_valid_o)} r{u(l1.vn0_ready_i)}]"
            f" vn1[v{u(l1.vn1_valid_i)} r{u(l1.vn1_ready_o)}"
            f" line={(u(l1.vn1_msg_i) >> (w - 32)) & ((1 << LINE_ADDR_W) - 1):#x}"
            f" st={STATE_NAMES[u(l1.vn1_state)]} ev={u(l1.vn1_event)}"
            f" fsm={u(l1.vn1_fsm_q)}]"
            f" vn2[v{u(l1.vn2_valid_i)}]")
    for t in range(NUM_TILES):
        n = nic_of(dut, t)
        held = []
        for v in range(6):
            if not u(n.rx_full_q[v]):
                continue
            m = u(n.rx_msg_q[v])
            mw = len(n.rx_msg_q[v])
            mt = m >> (mw - 5)
            held.append(
                f"vc{v}:{MSG_NAMES.get(mt, mt)}"
                f"/{(m >> (mw - 32)) & ((1 << LINE_ADDR_W) - 1):#x}"
                f"/src{(m >> (mw - 34)) & 3}")
        rxf = "".join(str(u(n.rx_full_q[v])) for v in range(6))
        rxb = "".join(str(u(n.rx_busy_q[v])) for v in range(6))
        pk = " ".join(f"vn{v}:{u(n.pk_q[v])}/vc{u(n.pk_vc_q[v])}"
                      for v in range(3))
        lines.append(
            f"  nic {t} rx_full={rxf} rx_busy={rxb}"
            f" out_vc_busy={u(n.out_vc_busy_q):06b}"
            f" has_credit={u(n.out_has_credit):06b}"
            f" pk[{pk}]"
            f" inj[v{u(n.flit_valid_o)}]"
            f" eje[v{u(n.flit_valid_i)}]"
            f" held[{', '.join(held) or '-'}]")
    for r in range(NUM_TILES):
        rt = router_of(dut, r)
        busy = u(rt.out_vc_busy_q)
        per_out = " ".join(
            f"{PORTS[o]}:{(busy >> (o * 6)) & 0x3f:06b}" for o in range(5))
        lines.append(f"  router {r} out_vc_busy[{per_out}]")
        for p in range(5):
            iu = rt.gen_input_unit[p].u_input_unit
            st = "".join(VC_ST[u(iu.vc_state_q[v])] for v in range(6))
            if st == "------":
                continue
            occ = "".join(str(u(iu.gen_vc_buf[v].u_vc_buf.count_q))
                          for v in range(6))
            op = "".join(PORTS[u(iu.vc_out_port_o[v])] for v in range(6))
            lines.append(f"    in {PORTS[p]} vc_state={st} occ={occ} out={op}")
    return "\n".join(lines)


class Tracer:
    """A ring buffer of recent messages, printed alongside a hang dump.

    A snapshot says what the machine is waiting for; it does not say how it got
    there, and for a deadlock the history is the diagnosis. This keeps the last
    few hundred message events and costs about twenty signal reads a cycle, so
    it is enabled only when a test asks for it.
    """

    def __init__(self, dut, depth: int = 600):
        self.dut = dut
        self.depth = depth
        self.buf = []
        self.l1 = [l1_of(dut, t) for t in range(NUM_TILES)]
        self.dirs = [dir_of(dut, t) for t in range(NUM_TILES)]
        self.w = len(self.l1[0].vn1_msg_i)
        self._last = {}

    def _fmt(self, raw) -> str:
        mt = raw >> (self.w - 5)
        from models.hangdump import MSG_NAMES
        return (f"{MSG_NAMES.get(mt, mt)}"
                f"/{(raw >> (self.w - 32)) & ((1 << LINE_ADDR_W) - 1):#x}"
                f"/src{(raw >> (self.w - 34)) & 3}"
                f"/dst{(raw >> (self.w - 36)) & 3}")

    def _push(self, s):
        self.buf.append(s)
        if len(self.buf) > self.depth:
            del self.buf[0]

    def sample(self, cyc: int):
        for t in range(NUM_TILES):
            l1 = self.l1[t]
            if u(l1.vn0_valid_o) and u(l1.vn0_ready_i):
                self._push(f"{cyc} t{t} sent {self._fmt(u(l1.vn0_msg_o))}")
            if u(l1.vn1_valid_i) and u(l1.vn1_ready_o):
                self._push(f"{cyc} t{t} took {self._fmt(u(l1.vn1_msg_i))} "
                           f"in {STATE_NAMES[u(l1.vn1_state)]}")
            if u(l1.vn2_valid_i):
                self._push(f"{cyc} t{t} got  {self._fmt(u(l1.vn2_msg_i))}")
            d = self.dirs[t]
            if u(d.vn1_valid_o) and u(d.vn1_ready_i):
                self._push(f"{cyc} b{t} sent {self._fmt(u(d.vn1_msg_o))}")
            if u(d.vn2_valid_o) and u(d.vn2_ready_i):
                self._push(f"{cyc} b{t} sent {self._fmt(u(d.vn2_msg_o))}")
            if u(d.fsm_q) == 4:
                key = (t, u(d.cur_q))
                if self._last.get(t) != key:
                    self._push(f"{cyc} b{t} exec ev{u(d.dir_event)} "
                               f"state{(u(d.old_meta_q) >> 7) & 7} "
                               f"sharers{(u(d.old_meta_q) >> 3) & 0xF} "
                               f"owner{(u(d.old_meta_q) >> 1) & 3} "
                               f"{self._fmt(u(d.cur_q))}")
                self._last[t] = key
            else:
                self._last[t] = None

    def text(self, keep: int = 200) -> str:
        return "\n".join("    " + x for x in self.buf[-keep:])
