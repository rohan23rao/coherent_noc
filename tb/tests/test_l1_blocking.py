"""L1 cache, blocking and coherence-free (Phase 4).

What these prove:
  * a load miss allocates, fills from memory and returns the right word, and
    the following access to the same line hits;
  * a store miss write-allocates and the stored value survives a round trip
    through eviction and refill, which is the only way to show the writeback
    actually carried data rather than just being issued;
  * a dirty victim is written back before its way is reused;
  * a store followed immediately by a load of the same word returns the stored
    value, exercising the S2->S1 forward rather than stale array data;
  * a long randomized sequence matches the golden atomic memory word for word.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from models.golden import GoldenMemory
from runner import HARNESS_DIR, RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"
MEM = RTL_DIR / "mem"

OP_LD, OP_ST = 0, 1
# The L1 index is addr[12:7], so two addresses collide in a set only if they
# differ by a multiple of 2^13. Conflict and eviction tests therefore need a
# memory window of several times 8 KB; 1024 lines gives four tags per set.
MEM_LINES = 1024
MEM_LATENCY = 6          # shortened so the randomized run is not dominated by it

MAX_ADDR = MEM_LINES * 32   # 32 KB


def _sources():
    return [lib("sram_1rw"), lib("rr_arbiter"), MEM / "mem_model.sv",
            L1 / "mshr_file.sv", L1 / "l1_cache.sv",
            HARNESS_DIR / "l1_mem_tb_top.sv"]


class CoreDriver:
    """Issues core requests and collects tagged responses."""

    def __init__(self, dut):
        self.dut = dut
        self.responses = {}
        self.next_tag = 0
        self.cycle = 0

    def idle(self):
        self.dut.core_req_valid_i.value = 0
        self.dut.core_op_i.value = OP_LD
        self.dut.core_addr_i.value = 0
        self.dut.core_wdata_i.value = 0
        self.dut.core_be_i.value = 0
        self.dut.core_tag_i.value = 0

    async def _collect(self):
        if u(self.dut.core_resp_valid_o):
            tag = u(self.dut.core_resp_tag_o)
            self.responses[tag] = u(self.dut.core_resp_rdata_o)

    async def issue(self, op, addr, wdata=0, be=0xF, timeout=500):
        """Issue one request and wait for its response. Returns (tag, rdata)."""
        tag = self.next_tag
        self.next_tag = (self.next_tag + 1) % 16
        d = self.dut
        d.core_req_valid_i.value = 1
        d.core_op_i.value = op
        d.core_addr_i.value = addr
        d.core_wdata_i.value = wdata
        d.core_be_i.value = be
        d.core_tag_i.value = tag

        for _ in range(timeout):
            accepted = u(d.core_req_ready_o) == 1
            await step(d)
            self.cycle += 1
            await self._collect()
            if accepted:
                break
        else:
            raise AssertionError(f"request op={op} addr={addr:#x} was never accepted")
        self.idle()

        for _ in range(timeout):
            if tag in self.responses:
                return tag, self.responses.pop(tag)
            await step(d)
            self.cycle += 1
            await self._collect()
        raise AssertionError(f"no response for op={op} addr={addr:#x} tag={tag}")


async def _setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = CoreDriver(dut)
    drv.idle()
    await reset_dut(dut, drive={})
    return drv, GoldenMemory()


@cocotb.test()
async def test_load_miss_then_hit(dut):
    """A cold load misses, fills from zeroed memory, and the next load hits."""
    drv, gold = await _setup(dut)

    _, got = await drv.issue(OP_LD, 0x100)
    assert got == gold.load(0x100) == 0, f"cold load returned {got:#x}, expected 0"

    before = drv.cycle
    _, got = await drv.issue(OP_LD, 0x104)
    cost = drv.cycle - before
    assert got == 0
    # Same line, so this must not have gone to memory.
    assert cost < MEM_LATENCY, (
        f"second access to the same line took {cost} cycles -- it missed when it "
        f"should have hit"
    )
    dut._log.info("load miss then same-line hit in %d cycles", cost)


@cocotb.test()
async def test_store_then_load_same_word(dut):
    """Store, then immediately load the same word: exercises the S2->S1 forward."""
    drv, gold = await _setup(dut)

    await drv.issue(OP_ST, 0x200, wdata=0xDEADBEEF)
    gold.store(0x200, 0xDEADBEEF)

    _, got = await drv.issue(OP_LD, 0x200)
    assert got == 0xDEADBEEF, (
        f"load after store returned {got:#x}, expected 0xDEADBEEF. A stale value "
        f"here means the S2 store write was not forwarded to S1."
    )
    assert got == gold.load(0x200)


@cocotb.test()
async def test_byte_enables(dut):
    """Partial-word stores merge rather than overwrite."""
    drv, gold = await _setup(dut)

    await drv.issue(OP_ST, 0x300, wdata=0xFFFFFFFF)
    gold.store(0x300, 0xFFFFFFFF)

    await drv.issue(OP_ST, 0x300, wdata=0x000000AA, be=0x1)
    gold.store(0x300, 0x000000AA, be=0x1)

    _, got = await drv.issue(OP_LD, 0x300)
    assert got == gold.load(0x300) == 0xFFFFFFAA, (
        f"byte-enabled store gave {got:#x}, expected 0xFFFFFFAA"
    )


@cocotb.test()
async def test_dirty_victim_is_written_back(dut):
    """Fill a set, evict a dirty line, and prove the data reached memory.

    Issuing a writeback is not the same as writing back the right data, so the
    check is that the value reappears after the line has been evicted and
    refetched -- which can only happen if the writeback carried it.
    """
    drv, gold = await _setup(dut)

    # Three addresses in the same set: index is addr[12:7], so stride 2^13.
    STRIDE = 1 << 13
    a0, a1, a2 = 0x80, 0x80 + STRIDE, 0x80 + 2 * STRIDE
    assert all(x < MAX_ADDR for x in (a0, a1, a2))

    await drv.issue(OP_ST, a0, wdata=0xCAFEF00D)
    gold.store(a0, 0xCAFEF00D)

    # Two more lines into a 2-way set evicts a0.
    await drv.issue(OP_LD, a1)
    await drv.issue(OP_LD, a2)

    # a0 must come back from memory with the stored value intact.
    _, got = await drv.issue(OP_LD, a0)
    assert got == gold.load(a0) == 0xCAFEF00D, (
        f"after eviction and refill a0 read {got:#x}, expected 0xCAFEF00D. "
        f"The dirty writeback either did not happen or carried the wrong line."
    )
    dut._log.info("dirty victim survived eviction and refill")


@cocotb.test()
async def test_clean_victim_is_not_written_back(dut):
    """A clean victim must be dropped silently, not written back.

    Checked by timing: evicting a clean line should cost one memory round trip
    (the fetch), not two (writeback plus fetch).
    """
    drv, _ = await _setup(dut)
    STRIDE = 1 << 13
    a0, a1, a2 = 0x480, 0x480 + STRIDE, 0x480 + 2 * STRIDE

    await drv.issue(OP_LD, a0)       # clean
    await drv.issue(OP_LD, a1)       # clean

    before = drv.cycle
    await drv.issue(OP_LD, a2)       # evicts a clean victim
    clean_cost = drv.cycle - before

    # Now make a set dirty and repeat, so the two are compared like for like.
    b0, b1, b2 = 0x500, 0x500 + STRIDE, 0x500 + 2 * STRIDE
    await drv.issue(OP_ST, b0, wdata=0x11111111)
    await drv.issue(OP_LD, b1)
    before = drv.cycle
    await drv.issue(OP_LD, b2)       # evicts a dirty victim
    dirty_cost = drv.cycle - before

    dut._log.info("eviction cost: clean %d cycles, dirty %d cycles", clean_cost, dirty_cost)
    assert dirty_cost > clean_cost, (
        f"evicting a dirty line ({dirty_cost} cycles) was not more expensive than "
        f"evicting a clean one ({clean_cost}) -- the clean victim is being "
        f"written back unnecessarily, or the dirty one is not being written back"
    )


@cocotb.test()
async def test_random_against_golden(dut):
    """Randomized loads and stores over a conflict-heavy footprint."""
    drv, gold = await _setup(dut)
    rng = random.Random(0x1CAC8E)

    STRIDE = 1 << 13
    # Four tags per set over eight sets: guarantees conflict misses, evictions
    # and writebacks rather than a footprint that fits in the cache.
    addrs = [
        (s * 128) + (t * STRIDE) + (w * 4)
        for s in range(8)
        for t in range(4)
        for w in range(2)
    ]
    addrs = [a for a in addrs if a < MAX_ADDR]
    assert len(addrs) >= 32

    for i in range(600):
        addr = rng.choice(addrs)
        if rng.random() < 0.45:
            wdata = rng.randrange(1 << 32)
            be = rng.choice([0xF, 0xF, 0x3, 0x1, 0xC])
            _, got = await drv.issue(OP_ST, addr, wdata=wdata, be=be)
            want = gold.store(addr, wdata, be)
            assert got == want, (
                f"op {i}: store to {addr:#x} returned {got:#x}, golden says {want:#x}"
            )
        else:
            _, got = await drv.issue(OP_LD, addr)
            want = gold.load(addr)
            assert got == want, (
                f"op {i}: load from {addr:#x} returned {got:#x}, golden says {want:#x}"
            )

    dut._log.info("600 randomized ops over %d addresses matched golden", len(addrs))


@pytest.mark.protocol
def test_l1_blocking():
    run(
        toplevel="l1_mem_tb_top",
        test_module="test_l1_blocking",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LATENCY_P": MEM_LATENCY},
    )
