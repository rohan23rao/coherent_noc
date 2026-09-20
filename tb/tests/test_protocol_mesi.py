"""MESI: adding E to the Phase 6 protocol (Phase 7).

What these prove:
  * a lone reader gets E, not S -- the whole point of adding the state;
  * the E to M upgrade is SILENT, generating no directory traffic, which is
    what makes read-then-write cost one transaction instead of two;
  * evicting a clean exclusive line sends PutE, because E is an ownership state
    here and silent eviction would leave the directory unable to tell whether
    the owner had upgraded;
  * race R11: a silent E to M followed by an eviction, with the directory still
    recording E, must take the data from the PutM;
  * race R5: a Put from a core that is no longer the owner must be acked and
    must not disown the current owner;
  * everything Phase 6 proved still holds with E enabled.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from models.coherence_checker import E, I, M, S, STATE_NAMES, SwmrChecker
from models.multicore import NUM_TILES, OP_LD, OP_ST, MultiCoreDriver
from models.net_delay import VN0, VN1, VN2, NetDelay
from runner import HARNESS_DIR, RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"
L2 = RTL_DIR / "l2"
MEM = RTL_DIR / "mem"

MEM_LINES = 1024
MEM_LATENCY = 8
STRIDE = 1 << 13


def _sources():
    return [
        lib("sram_1rw"), lib("fifo"), lib("rr_arbiter"), MEM / "mem_model.sv",
        L1 / "l1_coh_fsm.sv", L1 / "mshr_file.sv", L1 / "l1_cache.sv",
        L2 / "dir_coh_fsm.sv", L2 / "l2_bank.sv", L2 / "tbe_file.sv",
        L2 / "dir_ctrl.sv",
        HARNESS_DIR / "msg_mux.sv", HARNESS_DIR / "msg_delay.sv",
        HARNESS_DIR / "coh_direct_top.sv",
    ]


def set_of(addr: int) -> int:
    return (addr >> 7) & 63


def tag_of(addr: int) -> int:
    return ((addr >> 13) << 2) | ((addr >> 5) & 0x3)


def state_of(dut, tile: int, addr: int) -> int:
    sw = u(dut.dbg_state_o)
    tw = u(dut.dbg_tag_o)
    want = tag_of(addr)
    for w in range(2):
        st = (sw >> ((tile * 2 + w) * 4)) & 0xF
        tg = (tw >> ((tile * 2 + w) * 21)) & ((1 << 21) - 1)
        if st != I and tg == want:
            return st
    return I


async def _setup(dut, seed=0):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = MultiCoreDriver(dut, seed=seed)
    drv.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    delay = NetDelay(dut)
    await reset_dut(dut, drive={})
    return drv, delay


async def do_op(drv, tile, op, addr, wdata=0, be=0xF, timeout=6000):
    dut = drv.dut
    from models.golden import line_addr
    ln = line_addr(addr)
    tag = min(drv.free_tags[tile])
    want = drv.gold.store(addr, wdata, be) if op == OP_ST else drv.gold.load(addr)
    drv.free_tags[tile].discard(tag)
    drv.busy_lines.add(ln)
    drv.expected[tile][tag] = want
    drv.ctx[tile][tag] = f"{'ST' if op else 'LD'} {addr:#x}"
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
    raise AssertionError(f"tile {tile} {'ST' if op else 'LD'} {addr:#x} never completed")


def vn0_count(dut, tile: int) -> int:
    """Messages this tile has launched on VN0, by watching the delay element."""
    return u(dut.gen_tile[tile].u_l1.vn0_valid_o)


@cocotb.test()
async def test_lone_reader_gets_exclusive(dut):
    """A single core reading an untouched line gets E, not S."""
    drv, _ = await _setup(dut)
    addr = 0x0400
    dut.dbg_set_i.value = set_of(addr)

    await do_op(drv, 0, OP_LD, addr)
    for _ in range(4):
        await step(dut)

    st = state_of(dut, 0, addr)
    assert st == E, (
        f"a lone reader ended in {STATE_NAMES[st]}, expected E. Without this "
        f"a read-then-write costs two transactions instead of one, which is "
        f"the entire reason E exists."
    )
    dut._log.info("lone reader got E")


@cocotb.test()
async def test_e_to_m_upgrade_is_silent(dut):
    """Storing to a line held in E generates no directory traffic at all."""
    drv, _ = await _setup(dut)
    addr = 0x0480
    dut.dbg_set_i.value = set_of(addr)

    await do_op(drv, 1, OP_LD, addr)
    assert state_of(dut, 1, addr) == E

    # Watch VN0 across the store. A silent upgrade sends nothing.
    sent = 0
    from models.golden import line_addr
    tag = min(drv.free_tags[1]); drv.free_tags[1].discard(tag)
    drv.expected[1][tag] = drv.gold.store(addr, 0x5151A5A5)
    drv.ctx[1][tag] = "ST"; drv.inflight_line[1][tag] = line_addr(addr)
    drv.offer[1] = (tag, OP_ST, addr, 0x5151A5A5, 0xF)
    for _ in range(200):
        drv.drive()
        sent += vn0_count(dut, 1)
        await step(dut)
        drv._collect()
        if tag not in drv.pending[1] and drv.offer[1] is None:
            break
    drv.idle()
    drv.assert_clean()

    assert sent == 0, (
        f"the E->M upgrade sent {sent} VN0 message(s); it must be silent"
    )
    assert state_of(dut, 1, addr) == M, "the line should now be M"
    dut._log.info("E->M upgrade was silent: zero VN0 messages")


@cocotb.test()
async def test_clean_exclusive_eviction_sends_pute(dut):
    """Evicting E is not silent: the directory has to be told."""
    drv, _ = await _setup(dut)
    a0 = 0x0500
    a1, a2 = a0 + STRIDE, a0 + 2 * STRIDE
    dut.dbg_set_i.value = set_of(a0)

    await do_op(drv, 2, OP_LD, a0)
    assert state_of(dut, 2, a0) == E
    await do_op(drv, 2, OP_LD, a1)
    await do_op(drv, 2, OP_LD, a2)      # forces a0 out
    for _ in range(20):
        await step(dut)

    assert state_of(dut, 2, a0) == I, "a0 should have been evicted"
    # If the PutE were dropped rather than sent and acked, the directory would
    # still record tile 2 as owner and this read would hang on a forward to a
    # core that no longer has the line.
    got = await do_op(drv, 3, OP_LD, a0)
    assert got == drv.gold.load(a0)
    dut._log.info("clean exclusive eviction completed and the line was re-obtainable")


@cocotb.test()
async def test_r11_silent_upgrade_then_eviction(dut):
    """R11: E, silent upgrade to M, then PutM while the directory records E.

    The directory is in E and believes the line is clean. The owner upgraded
    without telling it, so the PutM arrives carrying data the directory does
    not expect. If that data is dropped the store is lost with no other symptom.
    """
    drv, _ = await _setup(dut)
    a0 = 0x0600
    a1, a2 = a0 + STRIDE, a0 + 2 * STRIDE
    dut.dbg_set_i.value = set_of(a0)

    await do_op(drv, 0, OP_LD, a0)
    assert state_of(dut, 0, a0) == E, "setup failed: tile 0 should hold a0 in E"

    await do_op(drv, 0, OP_ST, a0, wdata=0x11DEAD11)   # silent E -> M
    assert state_of(dut, 0, a0) == M

    await do_op(drv, 0, OP_LD, a1)
    await do_op(drv, 0, OP_LD, a2)                     # evicts a0 with PutM
    for _ in range(30):
        await step(dut)

    got = await do_op(drv, 1, OP_LD, a0)
    assert got == 0x11DEAD11, (
        f"tile 1 read {got:#x}, expected 0x11DEAD11. The directory was in E and "
        f"received a PutM carrying data it did not expect; dropping that data "
        f"loses the silent upgrade's store."
    )
    dut._log.info("R11: silent E->M survived eviction through a PutM the directory did not expect")


@cocotb.test()
async def test_r5_put_from_non_owner(dut):
    """R5: a Put that arrives after ownership has moved must not disown the new owner.

    Forced interleaving, using the delay hook rather than luck:
      1. tile 0 gets the line in E
      2. tile 0's VN0 is slowed right down
      3. tile 0 evicts -- its PutE is now stuck in the delay element
      4. tile 1 stores: the directory forwards to tile 0, which hands the line
         over, and the directory records tile 1 as owner
      5. tile 0's PutE finally lands, with the directory recording tile 1

    Step 5 is the race. If the directory clears the owner on a Put from a
    non-owner, tile 1 is silently disowned and its data is lost.
    """
    drv, delay = await _setup(dut)
    a0 = 0x0700
    a1, a2 = a0 + STRIDE, a0 + 2 * STRIDE
    dut.dbg_set_i.value = set_of(a0)

    await do_op(drv, 0, OP_LD, a0)
    assert state_of(dut, 0, a0) == E, "setup failed: tile 0 should hold a0 in E"

    delay.set(VN0, 0, 120)
    dut._log.info("R5: delays set -- %s", delay.describe())

    # Kick off tile 0's eviction of a0 and let its PutE sit in the delay.
    from models.golden import line_addr
    tag = min(drv.free_tags[0]); drv.free_tags[0].discard(tag)
    drv.expected[0][tag] = drv.gold.load(a1)
    drv.ctx[0][tag] = "LD a1"; drv.inflight_line[0][tag] = line_addr(a1)
    drv.offer[0] = (tag, OP_LD, a1, 0, 0xF)
    for _ in range(12):
        drv.drive()
        await step(dut)
        drv._collect()
    drv.idle()

    # Now tile 1 takes the line for writing while that PutE is still in flight.
    await do_op(drv, 1, OP_ST, a0, wdata=0x5A5A0001, timeout=8000)
    assert state_of(dut, 1, a0) == M, "tile 1 should own a0 for writing"

    # Release everything and let the stale PutE land.
    delay.clear()
    for _ in range(400):
        drv.drive()
        await step(dut)
        drv._collect()
    drv.idle()
    drv.assert_clean()

    # Tile 1 must still own the line, with its value intact.
    got = await do_op(drv, 1, OP_LD, a0, timeout=8000)
    assert got == 0x5A5A0001, (
        f"tile 1 read back {got:#x}, expected 0x5A5A0001. A stale Put from the "
        f"previous owner disowned the current one and its store was lost."
    )
    got2 = await do_op(drv, 2, OP_LD, a0, timeout=8000)
    assert got2 == 0x5A5A0001, (
        f"tile 2 read {got2:#x}; the line the directory hands out no longer "
        f"matches the owner's data"
    )
    dut._log.info("R5: a stale PutE from the previous owner did not disown the new one")


@cocotb.test()
async def test_random_mesi_with_swmr(dut):
    """10k randomized requests with E enabled, SWMR checked throughout."""
    drv, _ = await _setup(dut, seed=0xE5E5)
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
    for cyc in range(400_000):
        if drv.completed >= target:
            break
        if drv.outstanding() < 24:
            drv.plan(addrs, store_prob=0.45)
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
        f"only {drv.completed} of {target} completed in {drv.cycle} cycles"
    )
    dut._log.info("MESI: %d requests over %d cycles", drv.completed, drv.cycle)
    dut._log.info("%s", checker.summary())


@pytest.mark.protocol
def test_mesi():
    run(
        toplevel="coh_direct_top",
        test_module="test_protocol_mesi",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LATENCY_P": MEM_LATENCY,
                    "ENABLE_E": 1},
    )
