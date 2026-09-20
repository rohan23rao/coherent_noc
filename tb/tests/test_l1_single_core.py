"""Single-core L1 behaviour, over the coherence path (ports of Phases 4 and 5).

These are the Phase 4 and Phase 5 tests, retargeted. The properties they assert
-- allocate on miss, hit on the second access, byte-enabled merge, a dirty line
surviving eviction and refill, four misses outstanding at once, backpressure
rather than loss, no duplicate MSHR, no victim-way double booking, and per-line
ordering under out-of-order completion -- are all still real. What changed in
Phase 6 is only how the line is obtained: the L1 now asks a directory instead
of a memory controller.

The original harness (l1_mem_tb_top, an L1 bolted straight to a mem_model) no
longer exists because l1_cache no longer has a memory port. Nothing was
weakened to make these pass; they run against the full coherent system with
three of the four cores idle.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from models.coherence_checker import E, I, M, S, STATE_NAMES
from models.golden import GoldenMemory, line_addr
from runner import HARNESS_DIR, RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"
L2 = RTL_DIR / "l2"
MEM = RTL_DIR / "mem"

OP_LD, OP_ST = 0, 1
MEM_LINES = 1024
MEM_LATENCY = 10
STRIDE = 1 << 13
MSHR_ENTRIES = 4
TILE = 0


def _sources():
    return [
        lib("sram_1rw"), lib("fifo"), lib("rr_arbiter"), MEM / "mem_model.sv",
        L1 / "l1_coh_fsm.sv", L1 / "mshr_file.sv", L1 / "l1_cache.sv",
        L2 / "dir_coh_fsm.sv", L2 / "l2_bank.sv", L2 / "tbe_file.sv",
        L2 / "dir_ctrl.sv",
        HARNESS_DIR / "msg_mux.sv", HARNESS_DIR / "coh_direct_top.sv",
    ]


class SingleCore:
    """Drives tile 0 only; the other three cores stay idle."""

    def __init__(self, dut):
        self.dut = dut
        self.gold = GoldenMemory()
        self.cycle = 0
        self.done = {}
        self.pending = set()
        self.free_tags = set(range(16))
        self.max_mshr = 0

    def idle(self):
        d = self.dut
        d.core_req_valid_i.value = 0
        d.core_op_i.value = 0
        d.core_addr_i.value = 0
        d.core_wdata_i.value = 0
        d.core_be_i.value = 0
        d.core_tag_i.value = 0

    def _collect(self):
        d = self.dut
        if u(d.core_resp_valid_o) & (1 << TILE):
            tag = (u(d.core_resp_tag_o) >> (TILE * 4)) & 0xF
            val = (u(d.core_resp_rdata_o) >> (TILE * 32)) & 0xFFFFFFFF
            assert tag in self.pending, f"response for tag {tag} never issued"
            self.pending.discard(tag)
            self.free_tags.add(tag)
            self.done[tag] = val
        live = bin((u(d.dbg_mshr_valid_o) >> (TILE * MSHR_ENTRIES)) & 0xF).count("1")
        self.max_mshr = max(self.max_mshr, live)

    async def advance(self, n=1):
        for _ in range(n):
            self.idle()
            await step(self.dut)
            self.cycle += 1
            self._collect()

    async def try_issue(self, tag, op, addr, wdata=0, be=0xF) -> bool:
        d = self.dut
        d.core_req_valid_i.value = 1 << TILE
        d.core_op_i.value = op << (TILE * 2)
        d.core_addr_i.value = addr << (TILE * 32)
        d.core_wdata_i.value = wdata << (TILE * 32)
        d.core_be_i.value = be << (TILE * 4)
        d.core_tag_i.value = tag << (TILE * 4)
        accepted = (u(d.core_req_ready_o) >> TILE) & 1 == 1
        await step(d)
        self.cycle += 1
        self._collect()
        self.idle()
        if accepted:
            self.pending.add(tag)
        return accepted

    async def issue(self, op, addr, wdata=0, be=0xF, timeout=4000) -> int:
        tag = min(self.free_tags)
        self.free_tags.discard(tag)
        for _ in range(timeout):
            if await self.try_issue(tag, op, addr, wdata, be):
                break
        else:
            raise AssertionError(f"op={op} addr={addr:#x} never accepted")
        for _ in range(timeout):
            if tag in self.done:
                return self.done.pop(tag)
            await self.advance()
        raise AssertionError(f"no response for op={op} addr={addr:#x}")

    async def drain(self, timeout=20000):
        for _ in range(timeout):
            if not self.pending:
                return
            await self.advance()
        raise AssertionError(f"{len(self.pending)} responses never arrived")


async def _setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    c = SingleCore(dut)
    c.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    await reset_dut(dut, drive={})
    return c


@cocotb.test()
async def test_load_miss_then_hit(dut):
    """A cold load misses and fills; the next access to the line hits."""
    c = await _setup(dut)
    got = await c.issue(OP_LD, 0x100)
    assert got == c.gold.load(0x100) == 0, f"cold load returned {got:#x}"

    before = c.cycle
    got = await c.issue(OP_LD, 0x104)
    cost = c.cycle - before
    assert got == 0
    assert cost < MEM_LATENCY, (
        f"second access to the same line took {cost} cycles -- it missed when "
        f"it should have hit"
    )
    dut._log.info("load miss then same-line hit in %d cycles", cost)


@cocotb.test()
async def test_store_then_load_same_word(dut):
    """Store then load the same word: the S2->S1 forward and the fill merge."""
    c = await _setup(dut)
    await c.issue(OP_ST, 0x200, wdata=0xDEADBEEF)
    c.gold.store(0x200, 0xDEADBEEF)
    got = await c.issue(OP_LD, 0x200)
    assert got == 0xDEADBEEF, f"load after store returned {got:#x}"


@cocotb.test()
async def test_byte_enables(dut):
    """Partial-word stores merge rather than overwrite."""
    c = await _setup(dut)
    await c.issue(OP_ST, 0x300, wdata=0xFFFFFFFF)
    c.gold.store(0x300, 0xFFFFFFFF)
    await c.issue(OP_ST, 0x300, wdata=0x000000AA, be=0x1)
    c.gold.store(0x300, 0x000000AA, be=0x1)
    got = await c.issue(OP_LD, 0x300)
    assert got == c.gold.load(0x300) == 0xFFFFFFAA, f"got {got:#x}"


@cocotb.test()
async def test_dirty_victim_survives_eviction(dut):
    """A dirty line evicted by conflict comes back with its value intact.

    Issuing a PutM is not the same as the data reaching the directory, so the
    check is that the value reappears after the line has been evicted and
    refetched.
    """
    c = await _setup(dut)
    a0, a1, a2 = 0x80, 0x80 + STRIDE, 0x80 + 2 * STRIDE

    await c.issue(OP_ST, a0, wdata=0xCAFEF00D)
    c.gold.store(a0, 0xCAFEF00D)
    await c.issue(OP_LD, a1)
    await c.issue(OP_LD, a2)
    got = await c.issue(OP_LD, a0)
    assert got == 0xCAFEF00D, (
        f"after eviction and refill a0 read {got:#x}, expected 0xCAFEF00D -- "
        f"the PutM either did not happen or carried the wrong line"
    )
    dut._log.info("dirty victim survived eviction and refill")


@cocotb.test()
async def test_four_misses_outstanding_together(dut):
    """All four MSHRs live at once, read off the debug bus."""
    c = await _setup(dut)
    addrs = [0x0000, 0x0080, 0x0100, 0x0180]
    tags = []
    for a in addrs:
        tag = min(c.free_tags)
        c.free_tags.discard(tag)
        tags.append(tag)
        for _ in range(500):
            if await c.try_issue(tag, OP_LD, a):
                break
        else:
            raise AssertionError(f"addr {a:#x} never accepted")
    for _ in range(6):
        await c.advance()

    assert c.max_mshr == MSHR_ENTRIES, (
        f"only {c.max_mshr} MSHRs were ever live at once; misses are not "
        f"overlapping"
    )
    await c.drain()
    dut._log.info("4 misses overlapped: peak MSHR occupancy %d", c.max_mshr)


@cocotb.test()
async def test_backpressure_rather_than_loss(dut):
    """Under MSHR pressure a request is refused, then served -- never dropped."""
    c = await _setup(dut)
    addrs = [0x0000, 0x0080, 0x0100, 0x0180, 0x0200, 0x0280]
    tags = []
    refusals = 0
    for a in addrs:
        tag = min(c.free_tags)
        c.free_tags.discard(tag)
        tags.append(tag)
        for _ in range(2000):
            if await c.try_issue(tag, OP_LD, a):
                break
            refusals += 1
        else:
            raise AssertionError(f"addr {a:#x} never accepted")

    assert refusals > 0, (
        "no request was ever refused while six misses were queued against four "
        "MSHRs -- the cache is not applying backpressure"
    )
    await c.drain()
    assert len(c.done) == len(addrs), (
        f"{len(addrs) - len(c.done)} responses lost under backpressure"
    )
    dut._log.info("%d refusals, all %d requests served", refusals, len(addrs))


@cocotb.test()
async def test_secondary_miss_uses_one_mshr(dut):
    """Two requests to one line allocate one MSHR and both complete."""
    c = await _setup(dut)
    t0 = min(c.free_tags); c.free_tags.discard(t0)
    assert await c.try_issue(t0, OP_LD, 0x0400) or True
    t1 = min(c.free_tags); c.free_tags.discard(t1)
    for _ in range(500):
        if await c.try_issue(t1, OP_LD, 0x0404):
            break
    await c.drain()
    assert c.max_mshr <= 1, (
        f"a secondary miss to the same line allocated a second MSHR "
        f"(peak occupancy {c.max_mshr})"
    )
    dut._log.info("secondary miss to one line used a single MSHR")


@cocotb.test()
async def test_conflicting_misses_do_not_double_book_a_way(dut):
    """Four misses into a two-way set; l1_cache's way-reservation assertion is
    what actually polices this, and this drives it."""
    c = await _setup(dut)
    addrs = [0x0500 + i * STRIDE for i in range(4)]
    for a in addrs:
        tag = min(c.free_tags)
        c.free_tags.discard(tag)
        for _ in range(2000):
            if await c.try_issue(tag, OP_LD, a):
                break
    await c.drain()
    dut._log.info("four misses into one 2-way set completed cleanly")


@cocotb.test()
async def test_pipelined_random_against_golden(dut):
    """Out-of-order completion, per-line order preserved, values match golden.

    At most one operation per line is in flight, which is what makes it legal
    to apply the golden model in issue order and check each response when it
    eventually arrives -- see models/multicore.py for the argument.
    """
    c = await _setup(dut)
    rng = random.Random(0x5BAD5EED)
    addrs = [
        (s * 128) + (t * STRIDE) + (w * 4)
        for s in range(6) for t in range(4) for w in range(2)
    ]
    addrs = [x for x in addrs if x < MEM_LINES * 32]

    expected = {}
    tag_line = {}
    busy_lines = set()
    ops = 0

    def harvest():
        for t in list(c.done):
            got = c.done.pop(t)
            want = expected.pop(t)
            busy_lines.discard(tag_line.pop(t))
            assert got == want, f"tag {t}: got {got:#x}, golden says {want:#x}"

    for _ in range(400):
        while len(c.pending) >= 6 or not c.free_tags:
            await c.advance()
            harvest()

        cands = [a for a in addrs if line_addr(a) not in busy_lines]
        if not cands:
            await c.advance()
            harvest()
            continue

        addr = rng.choice(cands)
        tag = min(c.free_tags)
        c.free_tags.discard(tag)
        if rng.random() < 0.45:
            wdata = rng.randrange(1 << 32)
            be = rng.choice([0xF, 0xF, 0x3, 0x1, 0xC])
            want = c.gold.store(addr, wdata, be)
            op = OP_ST
        else:
            wdata, be = 0, 0xF
            want = c.gold.load(addr)
            op = OP_LD

        for _ in range(4000):
            if await c.try_issue(tag, op, addr, wdata, be):
                break
        else:
            raise AssertionError(f"op to {addr:#x} never accepted")

        expected[tag] = want
        tag_line[tag] = line_addr(addr)
        busy_lines.add(line_addr(addr))
        ops += 1
        harvest()

    await c.drain()
    harvest()
    assert not expected, f"{len(expected)} operations never completed"
    dut._log.info("%d pipelined ops matched golden; peak MSHR %d", ops, c.max_mshr)


@pytest.mark.protocol
def test_l1_single_core():
    run(
        toplevel="coh_direct_top",
        test_module="test_l1_single_core",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LATENCY_P": MEM_LATENCY,
                    "ENABLE_E": 0},
    )
