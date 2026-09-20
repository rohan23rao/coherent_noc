"""The twelve-race catalogue, each forced by a deterministic network delay.

Every test here does three things, and a race test that does fewer is not
worth having:

  1. **Forces** the interleaving with `NetDelay`, never by waiting longer. A
     race reproduced by waiting has not been reproduced.
  2. **Witnesses** it with `ArcProbe`: the exact table cell the race is about
     must have been presented to the controller. Without this a test that
     silently stops reaching the race keeps passing forever.
  3. **Checks the outcome** from a quiesced machine against values the test
     chose, so that deleting the handling arc makes the test fail rather than
     merely making it noisy.

`docs/races.md` records, for each race, the interleaving, the arc, this test's
name, and the result of deleting that arc.
"""

import cocotb
import pytest
from cocotb.clock import Clock

from models.coherence_checker import E, I, M, S, STATE_NAMES
from models.multicore import NUM_TILES, OP_LD, OP_ST, MultiCoreDriver
from models.net_delay import NetDelay
from models.probe import (ArcProbe, DEV_DATA, DEV_GETM, DEV_GETS,
                          DEV_PUTM_NON_OWNER, DEV_PUTM_OWNER, DEV_PUTS_LAST,
                          DEV_PUTS_NOT_LAST, DEV_PUTE_NON_OWNER, DIR_E, DIR_I,
                          DIR_M, DIR_S, DIR_S_D, EV_DATA_DIR_AGT0, EV_FWD_GETM,
                          EV_FWD_GETS, EV_INV, EV_INV_ACK, EV_PUT_ACK)
from models import raceutil as ru
from models import scenarios as sc
from runner import RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"; L2 = RTL_DIR / "l2"; MEM = RTL_DIR / "mem"
NOC = RTL_DIR / "noc"; TILE = RTL_DIR / "tile"; TOP = RTL_DIR / "top"

MEM_LINES = 4096
MEM_LAT = 8

# L1 transient states, by name, from coh_pkg::l1_state_e.
IM_AD, SM_AD, MI_A, II_A = 5, 7, 9, 12


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


async def _setup(dut, seed=0, verbose=False):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = MultiCoreDriver(dut, seed=seed)
    drv.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    delay = NetDelay(dut)
    await reset_dut(dut, drive={}, rst_name="arst_n")
    return drv, delay, ArcProbe(dut, verbose=verbose)


#=============================================================================
# R1. Early Inv-Ack: acks overtake the Data that says how many to expect.
#=============================================================================
@cocotb.test()
async def test_r1_early_inv_ack(dut):
    """Inv-Acks reach a core in IM_AD before the directory's Data+AckCount.

    Arc: `ack_cnt` is signed, and IM_AD + Inv-Ack decrements it and stays.
    """
    drv, delay, probe = await _setup(dut)
    a = ru.addr_of(bank=3, set_=8, tag=1)
    dut.dbg_set_i.value = sc.set_of(a)

    # Two sharers, so the GetM will carry an AckCount of 2.
    await sc.do_op(drv, 1, OP_LD, a, probe=probe)
    await sc.do_op(drv, 2, OP_LD, a, probe=probe)
    assert sc.state_of(dut, 1, a) == S and sc.state_of(dut, 2, a) == S, (
        "setup: tiles 1 and 2 should both hold the line in S"
    )

    # Hold the home bank's responses -- and only the bank's: the sharers sit on
    # VN2 too, and delaying them as well would delay the very acks that have to
    # overtake. Inv travels VN1 and is not held at all.
    delay.set_vn2_dir(ru.home_of(a), 40)

    val = 0xE471ACE5
    tag = ru.issue(drv, 0, OP_ST, a, wdata=val, expect=drv.gold.store(a, val))
    await ru.wait_done(dut, drv, 0, tag, probe, what="tile 0 GetM")
    delay.clear()
    await ru.wait_quiet(dut, drv, probe)

    probe.require_l1(
        0, IM_AD, EV_INV_ACK,
        "the Inv-Acks did not overtake the Data, so the early-ack case was "
        "never exercised -- raise the hold on the home bank's VN2.")
    assert probe.min_ack[0] < 0, (
        f"tile 0's ack_cnt never went negative (minimum {probe.min_ack[0]}). "
        f"That is the whole point of R1: an unsigned counter wraps here and "
        f"the transaction never completes.")

    assert sc.state_of(dut, 0, a) == M, "tile 0 should own the line in M"
    for t in (1, 2):
        assert sc.state_of(dut, t, a) == I, f"sharer {t} survived the GetM"
    drv.assert_clean()
    got = await sc.do_op(drv, 2, OP_LD, a)
    assert got == val, f"tile 2 read {got:#x}, expected {val:#x}"
    dut._log.info("R1: %d early Inv-Acks, ack_cnt reached %d before Data",
                  -probe.min_ack[0], probe.min_ack[0])


#=============================================================================
# R2. An upgrade loses the race: Inv arrives while the upgrader is in SM_AD.
#=============================================================================
@cocotb.test()
async def test_r2_upgrade_loses_the_race(dut):
    """Two sharers both store; the loser takes an Inv in SM_AD.

    Arc: SM_AD + Inv -> send Inv-Ack, -> IM_AD. The upgrade becomes a fetch.
    """
    drv, delay, probe = await _setup(dut)
    a0 = ru.addr_of(bank=3, set_=9, tag=2, word=0)
    a1 = ru.addr_of(bank=3, set_=9, tag=2, word=1)
    dut.dbg_set_i.value = sc.set_of(a0)

    await sc.do_op(drv, 0, OP_LD, a0, probe=probe)
    await sc.do_op(drv, 1, OP_LD, a0, probe=probe)
    assert sc.state_of(dut, 0, a0) == S and sc.state_of(dut, 1, a0) == S

    # Tile 0's GetM leaves its cache but is held in the network, so tile 0 sits
    # in SM_AD while tile 1's GetM is ordered first at the directory.
    delay.set_vn0(0, 30)
    v0, v1 = 0x0BADF00D, 0x1DEA1DEA
    t0 = ru.issue(drv, 0, OP_ST, a0, wdata=v0)
    await ru.tick(dut, drv, probe, n=6)
    t1 = ru.issue(drv, 1, OP_ST, a1, wdata=v1)

    await ru.wait_done(dut, drv, 1, t1, probe, what="tile 1 store")
    delay.clear()
    await ru.wait_done(dut, drv, 0, t0, probe, what="tile 0 store")
    await ru.wait_quiet(dut, drv, probe)

    probe.require_l1(
        0, SM_AD, EV_INV,
        "tile 0's GetM was not ordered second, so it never took an Inv while "
        "upgrading -- increase the hold on tile 0's VN0.")

    # Different words of one line, so the answer does not depend on which
    # store the directory ordered first -- only on neither being lost.
    drv.gold.store(a0, v0)
    drv.gold.store(a1, v1)
    for addr, want in ((a0, v0), (a1, v1)):
        got = await sc.do_op(drv, 2, OP_LD, addr)
        assert got == want, (
            f"word at {addr:#x} read {got:#x}, expected {want:#x}: the store "
            f"from the core that lost the upgrade race was lost with it")
    dut._log.info("R2: the losing upgrade took an Inv in SM_AD and refetched")


#=============================================================================
# R3. Writeback vs forward: a Fwd-GetM overtakes a PutM in flight.
#=============================================================================
@cocotb.test()
async def test_r3_writeback_vs_forward(dut):
    """The directory forwards to an owner that has already issued its PutM.

    Arc: MI_A + Fwd-GetM -> Data to the requester, -> II_A; and at the
    directory, PutM from a non-owner -> Put-Ack with no state change.
    """
    drv, delay, probe = await _setup(dut)
    # Three lines in one L1 set with three different tags. The home-bank bits
    # are part of the L1 tag, so varying the bank is enough -- no need for a
    # large address stride.
    a = ru.addr_of(bank=3, set_=17, tag=3, word=0)
    a_w1 = ru.addr_of(bank=3, set_=17, tag=3, word=1)
    b = ru.addr_of(bank=0, set_=17, tag=3)
    c = ru.addr_of(bank=1, set_=17, tag=3)
    dut.dbg_set_i.value = sc.set_of(a)
    home = ru.home_of(a)

    x = 0xDEADBE00
    await sc.do_op(drv, 0, OP_ST, a, wdata=x, probe=probe)
    assert sc.state_of(dut, 0, a) == M, "setup: tile 0 should hold a in M"
    await sc.do_op(drv, 0, OP_LD, b, probe=probe)  # other way; a is now LRU

    # From here tile 0's requests are stuck in the network: its PutM leaves the
    # cache, so the cache is in MI_A, but the directory has not seen it.
    delay.set_vn0(0, 200)
    tc = ru.issue(drv, 0, OP_LD, c, note="LD c, evicts a")
    await ru.wait_for(
        dut, drv, lambda: ru.l1_state_in_mshr(dut, probe, 0, a) == MI_A,
        probe, what="tile 0 reaching MI_A for the victim")

    y = 0x5EC0DE00
    t1 = ru.issue(drv, 1, OP_ST, a_w1, wdata=y)
    await ru.wait_done(dut, drv, 1, t1, probe, what="tile 1 store")

    probe.require_l1(
        0, MI_A, EV_FWD_GETM,
        "the forward did not reach tile 0 while its PutM was still in flight.")

    delay.clear()
    await ru.wait_done(dut, drv, 0, tc, probe, what="tile 0 LD c")
    await ru.wait_quiet(dut, drv, probe)

    probe.require_dir(
        home, DIR_M, DEV_PUTM_NON_OWNER,
        "the stale PutM never reached the directory after ownership moved.")
    probe.require_l1(
        0, II_A, EV_PUT_ACK,
        "tile 0 never completed its eviction from II_A.")

    drv.gold.store(a, x)
    drv.gold.store(a_w1, y)
    for addr, want in ((a, x), (a_w1, y)):
        got = await sc.do_op(drv, 2, OP_LD, addr)
        assert got == want, (
            f"word at {addr:#x} read {got:#x}, expected {want:#x}: the "
            f"forwarded data or the stale writeback overwrote a live value")
    dut._log.info("R3: a Fwd-GetM into MI_A handed the line on; the late PutM "
                  "did not disown the new owner")


@pytest.mark.protocol
def test_races():
    run(
        toplevel="system_top",
        test_module="test_races",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LAT": MEM_LAT, "ENABLE_E": 1},
    )
