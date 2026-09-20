"""MSI coherence over a direct connection, no network (Phase 6).

What these prove:
  * two cores can share a line, and both hold it in S;
  * a store invalidates the sharers and leaves exactly one core in M;
  * ownership migrates on a second core's store;
  * a read of a dirty line downgrades the owner to S and gives the reader S,
    which is the Fwd-GetS / S_D path;
  * the SWMR invariant holds continuously, checked on the implementation's own
    state arrays through the debug bus;
  * 10k randomized requests across four cores match the golden atomic memory.

E is disabled here (ENABLE_E=0), so the protocol is MSI. That is deliberate:
the hard transient-state debugging happens with four fewer arcs, and the
E-state bugs are isolated to Phase 7.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from models.coherence_checker import E, I, M, S, STATE_NAMES, SwmrChecker
from models.golden import line_addr
from models.multicore import NUM_TILES, OP_LD, OP_ST, MultiCoreDriver
from runner import HARNESS_DIR, RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"
L2 = RTL_DIR / "l2"
MEM = RTL_DIR / "mem"

MEM_LINES = 1024
MEM_LATENCY = 8
L1_IDX_LSB = 7
L1_SETS = 64


def _sources():
    return [
        lib("sram_1rw"), lib("fifo"), lib("rr_arbiter"),
        MEM / "mem_model.sv",
        L1 / "l1_coh_fsm.sv", L1 / "mshr_file.sv", L1 / "l1_cache.sv",
        L2 / "dir_coh_fsm.sv", L2 / "l2_bank.sv", L2 / "tbe_file.sv",
        L2 / "dir_ctrl.sv",
        HARNESS_DIR / "msg_mux.sv", HARNESS_DIR / "msg_delay.sv",
        HARNESS_DIR / "coh_direct_top.sv",
    ]


def set_of(addr: int) -> int:
    return (addr >> L1_IDX_LSB) & (L1_SETS - 1)


async def _setup(dut, seed=0):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = MultiCoreDriver(dut, seed=seed)
    drv.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    dut.delay_vn0_i.value = 0
    dut.delay_vn1_i.value = 0
    dut.delay_vn2_i.value = 0
    await reset_dut(dut, drive={})
    return drv


def read_states(dut, tile: int):
    """The (state, tag) of both ways of the currently selected set, for `tile`."""
    sw = u(dut.dbg_state_o)
    tw = u(dut.dbg_tag_o)
    out = []
    for w in range(2):
        st = (sw >> ((tile * 2 + w) * 4)) & 0xF
        tg = (tw >> ((tile * 2 + w) * 21)) & ((1 << 21) - 1)
        out.append((st, tg))
    return out


def state_of(dut, tile: int, addr: int) -> int:
    """The coherence state `tile` holds for `addr`, or I if it holds none."""
    tag = ((addr >> 13) << 2) | ((addr >> 5) & 0x3)
    for st, tg in read_states(dut, tile):
        if st != I and tg == tag:
            return st
    return I


async def do_op(drv, tile, op, addr, wdata=0, be=0xF, timeout=4000):
    """Issue one operation on one core and wait for it, checked against golden."""
    dut = drv.dut
    ln = line_addr(addr)
    tag = min(drv.free_tags[tile])
    if op == OP_ST:
        want = drv.gold.store(addr, wdata, be)
        ctx = f"ST {addr:#x}"
    else:
        want = drv.gold.load(addr)
        ctx = f"LD {addr:#x}"
    drv.free_tags[tile].discard(tag)
    drv.busy_lines.add(ln)
    drv.expected[tile][tag] = want
    drv.ctx[tile][tag] = ctx
    drv.inflight_line[tile][tag] = ln
    drv.offer[tile] = (tag, op, addr, wdata, be)

    for _ in range(timeout):
        drv.drive()
        await step(dut)
        drv.cycle += 1
        drv._collect()
        if tag not in drv.pending[tile] and drv.offer[tile] is None:
            drv.idle()
            drv.assert_clean()
            return want
    raise AssertionError(f"tile {tile} {ctx} never completed")


@cocotb.test()
async def test_two_cores_share_a_line(dut):
    """Two loads to one line leave both cores in S."""
    drv = await _setup(dut)
    addr = 0x0400
    dut.dbg_set_i.value = set_of(addr)

    await do_op(drv, 0, OP_LD, addr)
    await do_op(drv, 1, OP_LD, addr)
    for _ in range(4):
        await step(dut)

    s0 = state_of(dut, 0, addr)
    s1 = state_of(dut, 1, addr)
    assert s0 == S and s1 == S, (
        f"expected both cores in S, got tile0={STATE_NAMES[s0]} tile1={STATE_NAMES[s1]}"
    )
    dut._log.info("two cores share a line: both in S")


@cocotb.test()
async def test_store_invalidates_sharers(dut):
    """A store after sharing leaves exactly one core in M and the rest in I."""
    drv = await _setup(dut)
    addr = 0x0480
    dut.dbg_set_i.value = set_of(addr)

    await do_op(drv, 0, OP_LD, addr)
    await do_op(drv, 1, OP_LD, addr)
    await do_op(drv, 2, OP_ST, addr, wdata=0xA5A5A5A5)
    for _ in range(8):
        await step(dut)

    states = [state_of(dut, t, addr) for t in range(NUM_TILES)]
    writers = [t for t, s in enumerate(states) if s in (M, E)]
    readers = [t for t, s in enumerate(states) if s == S]
    assert writers == [2], (
        f"expected only tile 2 to hold the line for write, got "
        f"{[(t, STATE_NAMES[s]) for t, s in enumerate(states)]}"
    )
    assert not readers, (
        f"sharers survived the store: {[(t, STATE_NAMES[states[t]]) for t in readers]}"
    )

    # And the value is there.
    got = await do_op(drv, 2, OP_LD, addr)
    assert got == 0xA5A5A5A5
    dut._log.info("store invalidated both sharers; tile 2 alone in M")


@cocotb.test()
async def test_ownership_migrates(dut):
    """A second store moves ownership; the first owner ends in I."""
    drv = await _setup(dut)
    addr = 0x0500
    dut.dbg_set_i.value = set_of(addr)

    await do_op(drv, 0, OP_ST, addr, wdata=0x11111111)
    await do_op(drv, 3, OP_ST, addr, wdata=0x22222222)
    for _ in range(8):
        await step(dut)

    s0 = state_of(dut, 0, addr)
    s3 = state_of(dut, 3, addr)
    assert s3 == M, f"tile 3 should own the line, got {STATE_NAMES[s3]}"
    assert s0 == I, f"tile 0 should have lost the line, got {STATE_NAMES[s0]}"

    got = await do_op(drv, 1, OP_LD, addr)
    assert got == 0x22222222, f"read after migration returned {got:#x}"
    dut._log.info("ownership migrated 0 -> 3, and the newer value survived")


@cocotb.test()
async def test_read_of_a_dirty_line_downgrades_the_owner(dut):
    """Fwd-GetS path: the owner drops to S and the reader gets S with the data."""
    drv = await _setup(dut)
    addr = 0x0580
    dut.dbg_set_i.value = set_of(addr)

    await do_op(drv, 0, OP_ST, addr, wdata=0xDEC0DE01)
    got = await do_op(drv, 2, OP_LD, addr)
    assert got == 0xDEC0DE01, (
        f"reader got {got:#x}, not the owner's dirty value -- Fwd-GetS did not "
        f"deliver the data"
    )
    for _ in range(8):
        await step(dut)

    s0 = state_of(dut, 0, addr)
    s2 = state_of(dut, 2, addr)
    assert s0 == S, f"owner should have downgraded to S, got {STATE_NAMES[s0]}"
    assert s2 == S, f"reader should be in S, got {STATE_NAMES[s2]}"
    dut._log.info("dirty line read: owner downgraded to S, reader in S with the data")


@cocotb.test()
async def test_random_with_swmr_checker(dut):
    """10k randomized requests across four cores, SWMR checked throughout."""
    drv = await _setup(dut, seed=0xC0FFEE)
    rng = random.Random(0x5EED)

    # 16 lines over 8 sets, two tags per set: enough conflict to force evictions
    # while staying inside the modelled memory window.
    addrs = []
    for s in range(8):
        for tconf in range(2):
            base = (s * 128) + (tconf << 13)
            addrs.extend([base, base + 4])
    addrs = [a for a in addrs if a < MEM_LINES * 32]
    sets = sorted({set_of(a) for a in addrs})

    checker = SwmrChecker(dut, sets)
    dut.dbg_set_i.value = checker.current_set()

    target = 10_000
    issued = 0
    for cyc in range(400_000):
        if drv.completed >= target:
            break
        if issued - drv.completed < 24:
            drv.plan(addrs, store_prob=0.45)
            issued = drv.completed + drv.outstanding()
        drv.drive()
        await step(dut)
        drv.cycle += 1
        drv._collect()
        checker.check(cyc)
        if drv.mismatches:
            break

    drv.assert_clean()
    checker.assert_clean()
    assert drv.completed >= target, (
        f"only {drv.completed} of {target} requests completed in {drv.cycle} cycles"
    )
    dut._log.info("%d requests across 4 cores over %d cycles", drv.completed, drv.cycle)
    dut._log.info("%s", checker.summary())


@pytest.mark.protocol
def test_msi():
    run(
        toplevel="coh_direct_top",
        test_module="test_protocol_msi",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LATENCY_P": MEM_LATENCY,
                    "ENABLE_E": 0},
    )
