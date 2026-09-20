"""Inclusion and back-invalidation (Phase 9).

The L2 is strictly inclusive, so freeing a way means first removing the line
from every L1 that holds it. What makes this interesting -- and what R9 is
about -- is the case where the victim is held in M by some cache: capacity
pressure in a SHARED cache silently destroying a PRIVATE cache's dirty line.
The recall has to bring that dirty data back, and if it does not, a store that
was acknowledged long ago is simply lost with no other symptom.

The recall reuses the Fwd-GetM arc at the cache rather than adding a
data-carrying Inv (decision D17), so the L1 needs no new state-table entry.

Addresses here are chosen so that nine lines share one L2 set in one bank:
the L2 index is addr[12:7] and the bank is addr[6:5], so a stride of 2^13
lands every line in the same set of the same bank. Eight ways plus one forces
the eviction.
"""

import cocotb
import pytest
from cocotb.clock import Clock

from models.coherence_checker import E, I, M, S, STATE_NAMES
from models.multicore import NUM_TILES, OP_LD, OP_ST, MultiCoreDriver
from models.net_delay import NetDelay
from models import scenarios as sc
from runner import RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"; L2 = RTL_DIR / "l2"; MEM = RTL_DIR / "mem"
NOC = RTL_DIR / "noc"; TILE = RTL_DIR / "tile"; TOP = RTL_DIR / "top"

MEM_LINES = 4096          # must span nine 8 KB strides
MEM_LAT = 8
L2_STRIDE = 1 << 13       # same L2 set, same bank
L2_WAYS = 8


def _sources():
    return [
        lib("sram_1rw"), lib("fifo"), lib("rr_arbiter"), lib("credit_counter"),
        lib("reset_sync"), lib("msg_hold"), MEM / "mem_model.sv",
        L1 / "l1_coh_fsm.sv", L1 / "mshr_file.sv", L1 / "l1_cache.sv",
        L2 / "dir_coh_fsm.sv", L2 / "l2_bank.sv", L2 / "tbe_file.sv",
        L2 / "dir_ctrl.sv",
        NOC / "route_compute.sv", NOC / "input_unit.sv", NOC / "vc_allocator.sv",
        NOC / "switch_allocator.sv", NOC / "crossbar.sv", NOC / "router.sv",
        NOC / "noc_top.sv",
        TILE / "tile_nic.sv", TILE / "tile_top.sv", TOP / "system_top.sv",
    ]


async def _setup(dut, seed=0):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = MultiCoreDriver(dut, seed=seed)
    drv.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    NetDelay(dut)
    await reset_dut(dut, drive={}, rst_name="arst_n")
    return drv


@cocotb.test()
async def test_r9_back_invalidation_hits_a_line_in_m(dut):
    """R9: the L2 evicts a line an L1 holds dirty, and the data must survive.

    Setup, in order, so that the victim is deterministic -- the directory picks
    the lowest occupied way, which is the first line allocated:
      1. tile 0 stores to A, so A is in L2 way 0 and in tile 0's L1 in M
      2. seven more lines fill ways 1..7 of the same L2 set, driven from the
         other tiles so tile 0's own two-way L1 does not evict A
      3. a ninth line forces the L2 to evict way 0 -- which is A
    Step 3 is the race: a shared cache reclaiming a way destroys a private
    cache's dirty line, and only the recall carries the data out.
    """
    drv = await _setup(dut)
    base = 0x0080
    a = base
    fillers = [base + (k + 1) * L2_STRIDE for k in range(L2_WAYS - 1)]
    trigger = base + L2_WAYS * L2_STRIDE
    assert trigger < MEM_LINES * 32, "footprint escapes the modelled memory"

    dut.dbg_set_i.value = sc.set_of(a)

    # 1. tile 0 owns A dirty.
    await sc.do_op(drv, 0, OP_ST, a, wdata=0xD19E57ED)
    assert sc.state_of(dut, 0, a) == M, "setup: tile 0 should hold A in M"

    # 2. fill the rest of the L2 set from the other three tiles.
    for i, addr in enumerate(fillers):
        await sc.do_op(drv, 1 + (i % 3), OP_LD, addr)

    assert sc.state_of(dut, 0, a) == M, (
        "tile 0 lost A while the L2 set was being filled -- the fillers were "
        "supposed to avoid tile 0's L1"
    )

    # 3. one more line: the L2 must now recall A from tile 0.
    await sc.do_op(drv, 1, OP_LD, trigger)
    for _ in range(200):
        await step(dut)

    st = sc.state_of(dut, 0, a)
    assert st == I, (
        f"tile 0 still holds A in {STATE_NAMES[st]} after the L2 evicted it. "
        f"The L2 is strictly inclusive: a line it does not hold cannot be "
        f"resident in any L1."
    )

    # The dirty value must have survived the recall and reached memory.
    got = await sc.do_op(drv, 2, OP_LD, a)
    assert got == 0xD19E57ED, (
        f"tile 2 read {got:#x}, expected 0xD19E57ED. The recall did not carry "
        f"tile 0's dirty data out, so an acknowledged store was lost."
    )
    dut._log.info("R9: a dirty line recalled by L2 capacity pressure kept its data")


@cocotb.test()
async def test_back_invalidation_of_a_shared_line(dut):
    """The same path with the victim merely shared: Inv to each sharer, no data."""
    drv = await _setup(dut)
    base = 0x0100
    a = base
    fillers = [base + (k + 1) * L2_STRIDE for k in range(L2_WAYS - 1)]
    trigger = base + L2_WAYS * L2_STRIDE
    dut.dbg_set_i.value = sc.set_of(a)

    # Two tiles share A, so the recall has to invalidate both.
    await sc.do_op(drv, 0, OP_LD, a)
    await sc.do_op(drv, 3, OP_LD, a)
    assert sc.state_of(dut, 0, a) == S and sc.state_of(dut, 3, a) == S

    for i, addr in enumerate(fillers):
        await sc.do_op(drv, 1 + (i % 2), OP_LD, addr)
    await sc.do_op(drv, 1, OP_LD, trigger)
    for _ in range(200):
        await step(dut)

    for t in (0, 3):
        st = sc.state_of(dut, t, a)
        assert st == I, (
            f"tile {t} still holds the shared line in {STATE_NAMES[st]} after "
            f"the L2 evicted it -- inclusion is violated"
        )
    dut._log.info("shared line back-invalidated from both sharers")


@cocotb.test()
async def test_capacity_pressure_with_dirty_lines(dut):
    """Sustained pressure on one L2 set with stores from every core.

    Every line in the footprint shares one L2 set, so the directory is
    continuously recalling lines that caches hold dirty. The value check is the
    point: a recall that drops data loses a store, and the golden model is what
    notices.
    """
    drv = await _setup(dut, seed=0x9A9A)
    base = 0x0180
    addrs = [base + k * L2_STRIDE for k in range(12)]
    assert max(addrs) < MEM_LINES * 32

    for round_ in range(6):
        for i, addr in enumerate(addrs):
            tile = (i + round_) % NUM_TILES
            if (i + round_) % 3 == 0:
                await sc.do_op(drv, tile, OP_ST, addr,
                               wdata=0xC0DE0000 + round_ * 16 + i)
            else:
                await sc.do_op(drv, tile, OP_LD, addr)

    # Read everything back from a single core and check against golden.
    for addr in addrs:
        got = await sc.do_op(drv, 0, OP_LD, addr)
        want = drv.gold.load(addr)
        assert got == want, (
            f"addr {addr:#x} read {got:#x}, golden says {want:#x} -- a store "
            f"was lost across a back-invalidation"
        )
    drv.assert_clean()
    dut._log.info("%d ops over a 12-line footprint in one L2 set, all values intact",
                  drv.completed)


@pytest.mark.protocol
def test_inclusion():
    run(
        toplevel="system_top",
        test_module="test_protocol_inclusion",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LAT": MEM_LAT, "ENABLE_E": 1},
    )
