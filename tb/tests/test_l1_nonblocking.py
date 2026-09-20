"""Non-blocking L1 with the MSHR file (Phase 5).

What these prove:
  * four misses to distinct lines are outstanding *simultaneously*, observed on
    the MSHR debug bus rather than inferred from timing;
  * a fifth miss is refused until an MSHR frees, and is then served -- it
    stalls, it is not dropped;
  * a second request to a line that already has an MSHR does not allocate a
    second one (no merging, no duplicate), and still completes;
  * three concurrent misses to a two-way set do not double-book a victim way;
  * a deeply pipelined randomized stream matches the golden model, with
    responses arriving out of order.

Ordering note: same-line requests stall rather than merge, so per-line program
order is preserved. That is what makes it legal to apply the golden model in
issue order and compare each response when it eventually arrives.
"""

import random
from collections import deque

import cocotb
import pytest
from cocotb.clock import Clock

from models.golden import GoldenMemory
from runner import HARNESS_DIR, RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"
MEM = RTL_DIR / "mem"

OP_LD, OP_ST = 0, 1
MEM_LINES = 1024
MEM_LATENCY = 10          # long enough that misses genuinely overlap
MSHR_ENTRIES = 4
STRIDE = 1 << 13          # addresses this far apart share an L1 set


def _sources():
    return [lib("sram_1rw"), lib("rr_arbiter"), MEM / "mem_model.sv",
            L1 / "mshr_file.sv", L1 / "l1_cache.sv",
            HARNESS_DIR / "l1_mem_tb_top.sv"]


class PipelinedDriver:
    """Issues requests without waiting, and collects tagged responses."""

    def __init__(self, dut):
        self.dut = dut
        self.done = {}
        self.pending = deque()      # tags still outstanding
        self.cycle = 0
        self.max_mshr_seen = 0

    def idle(self):
        d = self.dut
        d.core_req_valid_i.value = 0
        d.core_op_i.value = OP_LD
        d.core_addr_i.value = 0
        d.core_wdata_i.value = 0
        d.core_be_i.value = 0
        d.core_tag_i.value = 0

    def _collect(self):
        d = self.dut
        if u(d.core_resp_valid_o):
            tag = u(d.core_resp_tag_o)
            assert tag in self.pending, f"response for tag {tag} that was never issued"
            self.pending.remove(tag)
            self.done[tag] = u(d.core_resp_rdata_o)
        live = bin(u(d.dbg_mshr_valid_o)).count("1")
        self.max_mshr_seen = max(self.max_mshr_seen, live)

    async def advance(self, n=1):
        for _ in range(n):
            self.idle()
            await step(self.dut)
            self.cycle += 1
            self._collect()

    async def try_issue(self, tag, op, addr, wdata=0, be=0xF) -> bool:
        """Offer a request for one cycle. Returns True if it was accepted."""
        d = self.dut
        d.core_req_valid_i.value = 1
        d.core_op_i.value = op
        d.core_addr_i.value = addr
        d.core_wdata_i.value = wdata
        d.core_be_i.value = be
        d.core_tag_i.value = tag
        accepted = u(d.core_req_ready_o) == 1
        await step(d)
        self.cycle += 1
        self._collect()
        self.idle()
        if accepted:
            self.pending.append(tag)
        return accepted

    async def issue(self, tag, op, addr, wdata=0, be=0xF, timeout=2000):
        for _ in range(timeout):
            if await self.try_issue(tag, op, addr, wdata, be):
                return
        raise AssertionError(f"request tag={tag} addr={addr:#x} never accepted")

    async def drain(self, timeout=20000):
        for _ in range(timeout):
            if not self.pending:
                return
            await self.advance()
        raise AssertionError(f"{len(self.pending)} responses never arrived: {list(self.pending)}")


async def _setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = PipelinedDriver(dut)
    drv.idle()
    await reset_dut(dut, drive={})
    return drv, GoldenMemory()


@cocotb.test()
async def test_four_misses_outstanding_together(dut):
    """All four MSHRs live at once, observed on the debug bus."""
    drv, gold = await _setup(dut)

    # Four distinct lines in four distinct sets: no way conflicts.
    addrs = [0x0000, 0x0080, 0x0100, 0x0180]
    for i, a in enumerate(addrs):
        await drv.issue(i, OP_LD, a)

    # Let them overlap in the memory engine.
    for _ in range(4):
        await drv.advance()

    assert drv.max_mshr_seen == MSHR_ENTRIES, (
        f"only {drv.max_mshr_seen} MSHRs were ever live at once; the cache is "
        f"not overlapping misses"
    )

    await drv.drain()
    for i, a in enumerate(addrs):
        assert drv.done[i] == gold.load(a), f"addr {a:#x} returned {drv.done[i]:#x}"
    dut._log.info("4 misses overlapped: peak MSHR occupancy %d", drv.max_mshr_seen)


@cocotb.test()
async def test_mshr_full_stalls_then_serves(dut):
    """A fifth miss is refused while the file is full, then served."""
    drv, gold = await _setup(dut)

    addrs = [0x0000, 0x0080, 0x0100, 0x0180]
    for i, a in enumerate(addrs):
        await drv.issue(i, OP_LD, a)

    # A fifth miss is accepted once -- into the replay slot -- because the
    # cache does not know it will miss until S1. What must NOT happen is a
    # sixth being accepted on top of it, which would overwrite the slot.
    await drv.issue(4, OP_LD, 0x0200)

    refused = 0
    for _ in range(8):
        if not await drv.try_issue(5, OP_LD, 0x0280):
            refused += 1
        else:
            break
    assert refused > 0, (
        "a sixth request was accepted while four MSHRs were live and a fifth "
        "was already replaying -- the replay slot is not applying backpressure "
        "and a request would be lost"
    )

    # Both must still complete, not be dropped.
    await drv.issue(5, OP_LD, 0x0280)
    await drv.drain()
    assert drv.done[4] == gold.load(0x0200)
    assert drv.done[5] == gold.load(0x0280)
    dut._log.info("sixth request refused for %d cycles; fifth and sixth both served", refused)


@cocotb.test()
async def test_secondary_miss_does_not_duplicate_mshr(dut):
    """Two requests to one line allocate one MSHR; the second still completes.

    mshr_file's a_no_duplicate_address assertion is the real check here -- it
    fires if the CAM ever lets a second entry onto the same line.
    """
    drv, gold = await _setup(dut)

    await drv.issue(0, OP_LD, 0x0400)
    await drv.issue(1, OP_LD, 0x0404)     # same line, different word

    await drv.drain()
    assert drv.done[0] == gold.load(0x0400)
    assert drv.done[1] == gold.load(0x0404)
    assert drv.max_mshr_seen <= 1, (
        f"a secondary miss to the same line allocated a second MSHR "
        f"(peak occupancy {drv.max_mshr_seen})"
    )
    dut._log.info("secondary miss to the same line used one MSHR and still completed")


@cocotb.test()
async def test_concurrent_misses_to_one_set_do_not_double_book(dut):
    """Three misses into a two-way set: the third must wait for a free way.

    Pseudo-LRU flips back after two allocations, so without an explicit
    reservation check the third MSHR would be handed a victim way that the
    first already owns and one of the two fills would be lost. l1_cache's
    a_no_way_double_booking assertion fires if that happens.
    """
    drv, gold = await _setup(dut)

    a = [0x0500, 0x0500 + STRIDE, 0x0500 + 2 * STRIDE, 0x0500 + 3 * STRIDE]
    for i, addr in enumerate(a):
        await drv.issue(i, OP_LD, addr)
    await drv.drain()

    for i, addr in enumerate(a):
        assert drv.done[i] == gold.load(addr), f"addr {addr:#x} wrong"
    dut._log.info("four misses into one 2-way set completed without double booking")


@cocotb.test()
async def test_store_then_load_same_line_ordered(dut):
    """A load behind an outstanding store to the same line sees the store."""
    drv, gold = await _setup(dut)

    await drv.issue(0, OP_ST, 0x0600, wdata=0x5EED1234)
    gold.store(0x0600, 0x5EED1234)
    await drv.issue(1, OP_LD, 0x0600)
    await drv.drain()

    assert drv.done[1] == 0x5EED1234, (
        f"load behind an outstanding store to the same line returned "
        f"{drv.done[1]:#x}, expected 0x5EED1234. Per-line order was not preserved."
    )


@cocotb.test()
async def test_pipelined_random_against_golden(dut):
    """Deeply pipelined randomized stream, responses out of order."""
    drv, gold = await _setup(dut)
    rng = random.Random(0x5BAD5EED)

    addrs = [
        (s * 128) + (t * STRIDE) + (w * 4)
        for s in range(6) for t in range(4) for w in range(2)
    ]
    addrs = [x for x in addrs if x < MEM_LINES * 32]

    expected = {}
    ctx = {}
    # Core tags are 4 bits, and responses complete OUT OF ORDER, so a tag must
    # not be reissued until its previous owner has actually responded --
    # cycling 0..15 is not enough. A free pool makes the invariant explicit.
    free_tags = set(range(16))
    ops = 0
    for _ in range(400):
        while len(drv.pending) >= 8 or not free_tags:
            await drv.advance()
            for done_tag in list(drv.done):
                assert drv.done[done_tag] == expected[done_tag], (
                    f"tag {done_tag}: got {drv.done[done_tag]:#x}, "
                    f"golden says {expected[done_tag]:#x} [{ctx[done_tag]}]"
                )
                del drv.done[done_tag]
                del expected[done_tag]
                free_tags.add(done_tag)

        addr = rng.choice(addrs)
        t = min(free_tags)
        free_tags.discard(t)
        if rng.random() < 0.45:
            wdata = rng.randrange(1 << 32)
            be = rng.choice([0xF, 0xF, 0x3, 0x1, 0xC])
            await drv.issue(t, OP_ST, addr, wdata=wdata, be=be)
            expected[t] = gold.store(addr, wdata, be)
            ctx[t] = f"ST addr={addr:#x} wdata={wdata:#010x} be={be:#x} op#{ops}"
        else:
            await drv.issue(t, OP_LD, addr)
            expected[t] = gold.load(addr)
            ctx[t] = f"LD addr={addr:#x} op#{ops}"
        ops += 1

        # Check anything that has come back.
        for done_tag in list(drv.done):
            assert drv.done[done_tag] == expected[done_tag], (
                f"tag {done_tag}: got {drv.done[done_tag]:#x}, "
                f"golden says {expected[done_tag]:#x} [{ctx[done_tag]}]"
            )
            del drv.done[done_tag]
            del expected[done_tag]
            free_tags.add(done_tag)

    await drv.drain()
    for done_tag, got in drv.done.items():
        assert got == expected[done_tag], (
            f"tag {done_tag}: got {got:#x}, golden says {expected[done_tag]:#x}"
        )

    dut._log.info(
        "%d pipelined ops matched golden; peak MSHR occupancy %d",
        ops, drv.max_mshr_seen,
    )
    assert drv.max_mshr_seen >= 2, (
        "the randomized run never had two misses outstanding -- it is not "
        "exercising the non-blocking path"
    )


@pytest.mark.protocol
def test_l1_nonblocking():
    run(
        toplevel="l1_mem_tb_top",
        test_module="test_l1_nonblocking",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LATENCY_P": MEM_LATENCY},
    )
