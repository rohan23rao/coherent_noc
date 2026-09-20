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
IM_AD, SM_AD, MI_A, EI_A, SI_A, II_A = 5, 7, 9, 10, 11, 12


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
        dut, drv, lambda: ru.l1_state_in_mshr(dut, 0, a) == MI_A,
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


#=============================================================================
# R4. Stale PutS: a sharer's eviction lands after the line has gone to M.
#=============================================================================
@cocotb.test()
async def test_r4_stale_puts(dut):
    """A PutS arrives at a directory that has since given the line to a writer.

    Arc: dir M + PutS -> Put-Ack, and *no state change*. The requester is not
    in the sharer vector any more, so there is nothing to remove and nothing to
    forget -- the temptation is to treat the Put as authoritative and drop the
    owner with it.
    """
    drv, delay, probe = await _setup(dut)
    a = ru.addr_of(bank=3, set_=20, tag=4)
    b = ru.addr_of(bank=0, set_=20, tag=4)
    c = ru.addr_of(bank=1, set_=20, tag=4)
    dut.dbg_set_i.value = sc.set_of(a)
    home = ru.home_of(a)

    await sc.do_op(drv, 0, OP_LD, a, probe=probe)
    await sc.do_op(drv, 2, OP_LD, a, probe=probe)
    assert sc.state_of(dut, 0, a) == S and sc.state_of(dut, 2, a) == S
    await sc.do_op(drv, 0, OP_LD, b, probe=probe)   # a becomes tile 0's LRU way

    delay.set_vn0(0, 200)
    tc = ru.issue(drv, 0, OP_LD, c, note="LD c, evicts a")
    await ru.wait_for(
        dut, drv, lambda: ru.l1_state_in_mshr(dut, 0, a) == SI_A,
        probe, what="tile 0 reaching SI_A for the evicted sharer")

    y = 0x4B1DE500
    t1 = ru.issue(drv, 1, OP_ST, a, wdata=y)
    await ru.wait_done(dut, drv, 1, t1, probe, what="tile 1 store")
    assert sc.state_of(dut, 1, a) == M, "tile 1 should own the line"

    delay.clear()
    await ru.wait_done(dut, drv, 0, tc, probe, what="tile 0 LD c")
    await ru.wait_quiet(dut, drv, probe)

    seen = probe.dir_states_for(home, DEV_PUTS_LAST) | \
           probe.dir_states_for(home, DEV_PUTS_NOT_LAST)
    assert DIR_M in seen, (
        "the stale PutS never reached the directory after the line went to M; "
        f"it was only ever seen in states {sorted(seen)}.\n{probe.summary()}")
    probe.require_l1(
        0, SI_A, EV_INV,
        "tile 0 was not invalidated while its PutS was in flight.")

    # If the Put had been treated as authoritative the directory would have
    # dropped tile 1 as owner, and this read would come from stale memory.
    drv.gold.store(a, y)
    got = await sc.do_op(drv, 2, OP_LD, a, probe=probe)
    assert got == y, (
        f"tile 2 read {got:#x}, expected {y:#x}: a stale PutS from a former "
        f"sharer disowned the current writer")
    assert sc.state_of(dut, 1, a) in (S,), (
        "tile 1 should have downgraded to S to serve the read, not been "
        "invalidated by the stale Put")
    dut._log.info("R4: a PutS that landed after the line went to M changed "
                  "nothing at the directory")


#=============================================================================
# R5. PutE from a non-owner: an eviction lands after ownership has moved.
#=============================================================================
@cocotb.test()
async def test_r5_pute_from_non_owner(dut):
    """The owner check on a Put is the difference between working and silent
    data loss. Arc: dir + PutE from a tile that is not the owner -> Put-Ack,
    owner untouched."""
    drv, delay, probe = await _setup(dut)
    a0 = 0x0700
    dut.dbg_set_i.value = sc.set_of(a0)
    home = ru.home_of(a0)
    msg = await sc.scenario_r5(dut, drv, delay, a0=a0, probe=probe)
    probe.require_dir_event(
        home, DEV_PUTE_NON_OWNER,
        "tile 0's PutE was not delayed past tile 1 taking the line, so the "
        "owner check was never exercised.")
    dut._log.info("%s (seen in dir states %s)", msg,
                  sorted(probe.dir_states_for(home, DEV_PUTE_NON_OWNER)))


#=============================================================================
# R6. Two GetMs back to back: the second forward finds the first requester
#     still waiting for its data.
#=============================================================================
@cocotb.test()
async def test_r6_two_getms_back_to_back(dut):
    """Directory M + GetM -> Fwd-GetM to the current owner, owner = requester.

    The directory hands the line on before the previous hand-off has finished,
    so the second Fwd-GetM reaches a cache in IM_AD. The arc that saves it is
    IM_AD + Fwd-GetM -> stall: a cache cannot forward data it does not have,
    and stalling on VN1 is legal precisely because VN2 is a sink and the data
    is already on its way.
    """
    drv, delay, probe = await _setup(dut)
    a0 = ru.addr_of(bank=3, set_=24, tag=6, word=0)
    a1 = ru.addr_of(bank=3, set_=24, tag=6, word=1)
    a2 = ru.addr_of(bank=3, set_=24, tag=6, word=2)
    dut.dbg_set_i.value = sc.set_of(a0)
    home = ru.home_of(a0)

    v0 = 0x60000000
    await sc.do_op(drv, 0, OP_ST, a0, wdata=v0, probe=probe)
    assert sc.state_of(dut, 0, a0) == M

    # Tile 0's hand-off to tile 1 is slow, so tile 1 is still in IM_AD when the
    # directory forwards tile 2's GetM to it.
    delay.set_vn2(0, 60)
    v1, v2 = 0x61111111, 0x62222222
    t1 = ru.issue(drv, 1, OP_ST, a1, wdata=v1)
    await ru.tick(dut, drv, probe, n=10)
    t2 = ru.issue(drv, 2, OP_ST, a2, wdata=v2)

    await ru.wait_done(dut, drv, 1, t1, probe, what="tile 1 store")
    delay.clear()
    await ru.wait_done(dut, drv, 2, t2, probe, what="tile 2 store")
    await ru.wait_quiet(dut, drv, probe)

    assert probe.count_dir(home, DIR_M, DEV_GETM) >= 2, (
        f"the directory served only "
        f"{probe.count_dir(home, DIR_M, DEV_GETM)} GetM(s) from M; the two "
        f"stores did not queue up behind one another.\n{probe.summary()}")
    probe.require_l1(
        1, IM_AD, EV_FWD_GETM,
        "the second forward did not reach tile 1 while it was still waiting "
        "for the first hand-off -- raise the hold on tile 0's VN2.")

    states = [sc.state_of(dut, t, a0) for t in range(NUM_TILES)]
    writers = [t for t, st in enumerate(states) if st in (M, E)]
    assert writers == [2], (
        f"expected tile 2 alone to hold the line for write, got "
        f"{[(t, STATE_NAMES[st]) for t, st in enumerate(states)]}")

    for addr, want in ((a0, v0), (a1, v1), (a2, v2)):
        drv.gold.store(addr, want)
    for addr, want in ((a0, v0), (a1, v1), (a2, v2)):
        got = await sc.do_op(drv, 3, OP_LD, addr, probe=probe)
        assert got == want, (
            f"word at {addr:#x} read {got:#x}, expected {want:#x}: a store was "
            f"lost as the line was passed on")
    dut._log.info("R6: %d GetMs served from M; the second forward stalled in "
                  "IM_AD until the data arrived",
                  probe.count_dir(home, DIR_M, DEV_GETM))


#=============================================================================
# R7. A GetS arrives at a directory in S_D, waiting for the owner's data.
#=============================================================================
@cocotb.test()
async def test_r7_gets_into_s_d(dut):
    """Directory S_D + GetS -> stall, and that stall is load-bearing.

    In S_D the directory has forwarded a GetS to the owner and has not yet been
    given the data. Its own L2 copy is stale by construction -- the owner wrote
    to the line without telling it. Serving a third core from that copy returns
    a value that was overwritten long ago, with nothing anywhere to notice.
    """
    drv, delay, probe = await _setup(dut)
    a = ru.addr_of(bank=3, set_=26, tag=7)
    dut.dbg_set_i.value = sc.set_of(a)
    home = ru.home_of(a)

    x = 0x57A1EDA7
    await sc.do_op(drv, 0, OP_ST, a, wdata=x, probe=probe)
    assert sc.state_of(dut, 0, a) == M, "setup: tile 0 must hold the line dirty"

    # The owner's response is slow, so the directory sits in S_D. Both the data
    # to the reader and the writeback to the bank are on tile 0's VN2.
    delay.set_vn2(0, 80)
    t1 = ru.issue(drv, 1, OP_LD, a)
    await ru.wait_for(
        dut, drv, lambda: probe.count_l1(0, M, EV_FWD_GETS) > 0, probe,
        what="the owner being asked to downgrade")
    t2 = ru.issue(drv, 2, OP_LD, a)
    await ru.tick(dut, drv, probe, n=20)

    probe.require_dir(
        home, DIR_S_D, DEV_GETS,
        "the second reader's GetS did not arrive while the directory was "
        "still waiting for the owner's data.")

    delay.clear()
    await ru.wait_done(dut, drv, 1, t1, probe, what="tile 1 load")
    await ru.wait_done(dut, drv, 2, t2, probe, what="tile 2 load")
    await ru.wait_quiet(dut, drv, probe)

    drv.gold.store(a, x)
    for t in (1, 2, 3):
        got = await sc.do_op(drv, t, OP_LD, a, probe=probe)
        assert got == x, (
            f"tile {t} read {got:#x}, expected {x:#x}: the directory answered "
            f"from its stale L2 copy while it was in S_D")
    dut._log.info("R7: a GetS into S_D waited for the owner's data instead of "
                  "answering from the stale L2 copy")


#=============================================================================
# R8. A forward into a dead MSHR -- proved impossible rather than handled.
#=============================================================================
@cocotb.test()
async def test_r8_no_forward_into_a_dead_mshr(dut):
    """II_A must never be shown a forward, and there is no arc for it.

    II_A means: this cache has given the line up, answered someone else's
    forward, and is waiting only for the Put-Ack that retires the entry. A
    forward arriving now would have no data to answer with. Rather than adding
    a permissive arc, the table leaves the cell blank and the RTL asserts on
    it, because the case is *impossible* for a reason worth being able to
    state: the directory removes a requester from its sharer vector -- or
    stops recording it as owner -- before it sends the Put-Ack, so after the
    ack there is nothing left that could name that cache in a forward.

    This test drives the window as wide as it can be driven: a cache sits in
    II_A while two other cores fight over the same line. The pass condition is
    that nothing but a Put-Ack is ever delivered to it.
    """
    drv, delay, probe = await _setup(dut)
    a = ru.addr_of(bank=3, set_=28, tag=8, word=0)
    a1 = ru.addr_of(bank=3, set_=28, tag=8, word=1)
    b = ru.addr_of(bank=0, set_=28, tag=8)
    c = ru.addr_of(bank=1, set_=28, tag=8)
    dut.dbg_set_i.value = sc.set_of(a)

    x = 0x8DEAD000
    await sc.do_op(drv, 0, OP_ST, a, wdata=x, probe=probe)
    await sc.do_op(drv, 0, OP_LD, b, probe=probe)

    delay.set_vn0(0, 250)
    tc = ru.issue(drv, 0, OP_LD, c, note="LD c, evicts a")
    await ru.wait_for(
        dut, drv, lambda: ru.l1_state_in_mshr(dut, 0, a) == MI_A,
        probe, what="tile 0 reaching MI_A")

    y = 0x8B0B0000
    t1 = ru.issue(drv, 1, OP_ST, a1, wdata=y)
    await ru.wait_for(
        dut, drv, lambda: ru.l1_state_in_mshr(dut, 0, a) == II_A,
        probe, what="tile 0 reaching II_A after answering the forward")

    # Tile 0 is now in II_A with its Put-Ack still unreachable. Two more cores
    # take the line in turn; every forward they provoke must go to the current
    # owner and none of them to tile 0.
    t2 = ru.issue(drv, 2, OP_LD, a)
    await ru.wait_done(dut, drv, 2, t2, probe, what="tile 2 load")
    t3 = ru.issue(drv, 3, OP_ST, a1, wdata=y ^ 0xFF)
    await ru.wait_done(dut, drv, 3, t3, probe, what="tile 3 store")

    in_iia = probe.l1_events_in(0, II_A)
    assert in_iia <= {EV_PUT_ACK}, (
        f"tile 0 was shown {sorted(in_iia)} while in II_A; only a Put-Ack is "
        f"reachable there. A forward here means the directory generated one "
        f"for a cache it had already acknowledged.\n{probe.summary()}")

    delay.clear()
    await ru.wait_done(dut, drv, 0, tc, probe, what="tile 0 LD c")
    await ru.wait_done(dut, drv, 1, t1, probe, what="tile 1 store")
    await ru.wait_quiet(dut, drv, probe)
    probe.require_l1(0, II_A, EV_PUT_ACK,
                     "tile 0 never actually reached II_A, so the window this "
                     "test is about was never open.")

    drv.gold.store(a, x)
    drv.gold.store(a1, y ^ 0xFF)
    for addr, want in ((a, x), (a1, y ^ 0xFF)):
        got = await sc.do_op(drv, 2, OP_LD, addr, probe=probe)
        assert got == want, (
            f"word at {addr:#x} read {got:#x}, expected {want:#x}")
    dut._log.info("R8: tile 0 held II_A across two ownership changes and was "
                  "shown nothing but its Put-Ack")


#=============================================================================
# R9. Back-invalidation hits a line held in M. Body shared with the inclusion
#     tier so the two cannot drift.
#=============================================================================
@cocotb.test()
async def test_r9_back_invalidation_hits_m(dut):
    """An L2 capacity eviction recalls a line an L1 holds dirty."""
    drv, delay, probe = await _setup(dut)
    msg = await sc.scenario_r9(dut, drv, probe=probe, base=0x0080,
                               mem_lines=MEM_LINES)
    # The recall reuses the Fwd-GetM arc at the cache -- that reuse is the
    # decision (D17), so it is what the witness checks.
    probe.require_l1(
        0, M, EV_FWD_GETM,
        "the recall never reached tile 0 as a Fwd-GetM, so the reuse the "
        "design depends on was not exercised.")
    dut._log.info("%s", msg)


#=============================================================================
# R10. False sharing storm. No arc -- this one is a measurement.
#=============================================================================
@cocotb.test()
async def test_r10_false_sharing_storm(dut):
    """Four cores store to four distinct words of one line, round-robin.

    Nothing here is incorrect and there is no arc to delete. The point is the
    cost: the cores share no data at all, and the line still moves on every
    store. The number this prints -- ownership transfers per store -- is the
    one worth being able to quote, because it is what padding a structure to a
    cache line buys back.
    """
    drv, delay, probe = await _setup(dut)
    words = [ru.addr_of(bank=3, set_=30, tag=10, word=w) for w in range(4)]
    dut.dbg_set_i.value = sc.set_of(words[0])
    home = ru.home_of(words[0])
    rounds = 8

    for r in range(rounds):
        for t in range(NUM_TILES):
            v = 0xA0000000 | (r << 8) | t
            drv.gold.store(words[t], v)
            tag = ru.issue(drv, t, OP_ST, words[t], wdata=v)
            await ru.wait_done(dut, drv, t, tag, probe,
                               what=f"round {r} tile {t} store")
    await ru.wait_quiet(dut, drv, probe)

    stores = rounds * NUM_TILES
    getm_from_m = probe.count_dir(home, DIR_M, DEV_GETM)
    fwds = sum(probe.count_l1(t, M, EV_FWD_GETM) for t in range(NUM_TILES))
    assert getm_from_m >= stores - NUM_TILES, (
        f"only {getm_from_m} of {stores} stores found the line already owned "
        f"elsewhere; the line did not ping-pong, so this is not the scenario "
        f"the test claims to measure.\n{probe.summary()}")

    for t in range(NUM_TILES):
        got = await sc.do_op(drv, t, OP_LD, words[t], probe=probe)
        assert got == drv.gold.load(words[t]), (
            f"tile {t} lost its own word under false sharing: read {got:#x}, "
            f"expected {drv.gold.load(words[t]):#x}")
    dut._log.info(
        "R10: %d stores to 4 disjoint words of one line cost %d ownership "
        "transfers and %d cache-to-cache forwards -- %.2f transfers per store, "
        "for data that is never actually shared",
        stores, getm_from_m, fwds, getm_from_m / stores)


#=============================================================================
# R11. Silent E->M, then an eviction the directory did not expect.
#=============================================================================
@cocotb.test()
async def test_r11_silent_upgrade_then_eviction(dut):
    """A PutM arrives for a line the directory still records as E.

    Arc: dir E + PutM from the owner -> copy the data into the L2, -> I. The
    directory cannot tell whether a core in E silently upgraded, so it must
    accept data it did not ask for. Dropping it loses the store with no other
    symptom.
    """
    drv, delay, probe = await _setup(dut)
    a0 = 0x0600
    dut.dbg_set_i.value = sc.set_of(a0)
    home = ru.home_of(a0)
    msg = await sc.scenario_r11(dut, drv, a0=a0, probe=probe)
    probe.require_dir(
        home, DIR_E, DEV_PUTM_OWNER,
        "the directory was not in E when the PutM arrived, so it was never "
        "asked to accept data for a line it thought was clean.")
    dut._log.info("%s", msg)


#=============================================================================
# R12. A blocked virtual network must not stop the others.
#=============================================================================
@cocotb.test()
async def test_r12_blocked_vn0_does_not_stop_vn1_vn2(dut):
    """Saturate VN0 into one bank and require VN1 and VN2 to keep draining.

    This is the failure that separate virtual networks exist to prevent, and
    the one that survived every direct-connect test: a request that cannot
    make progress holds a virtual channel, and if anything shared -- an
    allocator, an ejection port, a credit pool -- lets that block the response
    behind it, the responses that would have unblocked the request never
    arrive. The liveness assertions on the MSHR and the TBE are the detector;
    a deadlock here does not produce a wrong value, it produces silence.
    """
    drv, delay, probe = await _setup(dut, seed=0xC12)
    # Every line homed at one bank, and few enough of them that all four cores
    # collide constantly -- so VN0 into that bank stays saturated while VN1
    # forwards and VN2 responses have to cross the same links.
    hot = [ru.addr_of(bank=0, set_=s, tag=12) for s in range(6)]
    dut.dbg_set_i.value = sc.set_of(hot[0])

    # One tile's requests are held for a long time, so a VN0 packet is parked
    # in the network the whole run rather than merely being slow.
    delay.set_vn0(2, 200)

    target = 600
    for _ in range(200_000):
        if drv.completed >= target:
            break
        if drv.outstanding() < 16:
            drv.plan(hot, store_prob=0.5)
        await ru.tick(dut, drv, probe)
    delay.clear()
    await ru.wait_quiet(dut, drv, probe, max_cycles=20_000)

    drv.assert_clean()
    assert drv.completed >= target, (
        f"only {drv.completed} of {target} requests completed in {drv.cycle} "
        f"cycles with one source held -- forward progress was lost")
    dut._log.info(
        "R12: %d requests over %d cycles into one bank with a VN0 source held "
        "200 cycles; every MSHR and TBE retired inside its liveness bound",
        drv.completed, drv.cycle)


@pytest.mark.protocol
def test_races():
    run(
        toplevel="system_top",
        test_module="test_races",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LAT": MEM_LAT, "ENABLE_E": 1},
    )
