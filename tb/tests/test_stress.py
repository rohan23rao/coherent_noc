"""Constrained-random stress, and the coverage report that says what it reached.

Four runs, in one simulation so their coverage accumulates:

  three **checked** runs, over footprints of 4, 16 and 256 lines. One
  operation per line at a time, so the golden atomic memory has a defined
  answer for every response and every one is checked. A 4-line footprint in a
  two-way L1 thrashes constantly -- evictions, back-invalidations, capacity
  pressure at both levels; 256 lines exercises the miss and fill paths instead.

  one **racing** run, where that exclusivity is lifted and several cores have
  operations in flight on one line at once. This is the only way the
  transient-state forward arcs are reachable: with one operation per line a
  forward can never meet a miss on the same line, which is exactly why bug B16
  survived 10,000 random requests. The price is that no single response has a
  predictable value, so the checks are the SWMR monitor, every assertion in
  the design, and a weaker value check -- a load must return something
  somebody stored.

Per-virtual-network delays are re-randomised throughout, so no run depends on
one arbitration order. The seed is fixed and printed: a failing run has to be
reproducible.
"""

import os

import cocotb
import pytest
from cocotb.clock import Clock

from models.coherence_checker import SwmrChecker
from models import hangdump
from models.coverage import Coverage
from models.probe import LivenessGauge
from models.multicore import NUM_TILES, MultiCoreDriver
from models.net_delay import NetDelay
from models import raceutil as ru
from models import scenarios as sc
from runner import RTL_DIR, lib, run
from tbutil import reset_dut, step, u

L1 = RTL_DIR / "l1"; L2 = RTL_DIR / "l2"; MEM = RTL_DIR / "mem"
NOC = RTL_DIR / "noc"; TILE = RTL_DIR / "tile"; TOP = RTL_DIR / "top"

MEM_LINES = 16384          # must span the 256-line footprint's stride
MEM_LAT = 8
# The liveness bounds for this build. The stress tier deliberately injects
# holds of up to 200 cycles to open the windows the stale-Put races need, so
# the bounds have to be given the corresponding slack -- and every run prints
# the worst age it actually observed against them, which is the number that
# says whether the slack was needed or merely granted.
TBE_TO = 20000
MSHR_TO = 40000
# The gate is 100k per footprint. STRESS_REQUESTS lowers it for a smoke run
# during development; every run prints the number it actually completed, so a
# lowered run cannot be mistaken for the gate.
REQUESTS = int(os.environ.get("STRESS_REQUESTS", 100_000))
# STRESS_HANG=<cycles> dumps the whole machine the moment any MSHR or TBE
# has been live that long, instead of waiting for the bound to fire.
_HANG = int(os.environ.get("STRESS_HANG", 0))

_COV = None                # accumulated across every test in this module


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


def footprint(lines: int) -> list:
    """`lines` distinct cache lines, spread over banks, sets and tags.

    Two words per line, so a byte-enabled store and a load can land in
    different words of one line -- false sharing arrives for free rather than
    having to be arranged.
    """
    addrs = []
    s = t = 0
    while len(addrs) < lines * 2:
        bank = (len(addrs) // 2) % NUM_TILES
        set_ = ((len(addrs) // 2) // NUM_TILES) % 64
        tag = (len(addrs) // 2) // (NUM_TILES * 64)
        base = ru.addr_of(bank=bank, set_=set_, tag=tag)
        addrs.extend([base, base + 8])
    assert max(addrs) < MEM_LINES * 32, "footprint escapes the modelled memory"
    return addrs


async def _setup(dut, seed):
    global _COV
    # Per test, not once per module: cocotb cancels a test's tasks when it
    # ends, so a clock started in the first test stops before the second.
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = MultiCoreDriver(dut, seed=seed)
    drv.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    delay = NetDelay(dut)
    await reset_dut(dut, drive={}, rst_name="arst_n")
    if _COV is None:
        _COV = Coverage(dut)
    return drv, delay, _COV


def _reshuffle(delay, rng):
    """Re-randomise the per-virtual-network holds.

    Mostly small, occasionally large. The small values keep changing which
    arbitration order a run happens to take; the large ones open windows wide
    enough for a message to be overtaken, which is what makes the transient
    arcs reachable without arranging each one by hand.

    Requests get the long holds and responses do not, and that asymmetry is
    deliberate. A stale Put -- the whole E-state family of races -- needs a
    request delayed long enough for the line's directory state to have moved
    on twice, which is hundreds of cycles. Delaying a RESPONSE that long
    instead would push a directory transaction past its liveness bound and
    fail the run for a reason the test did not intend to create.
    """
    for src in range(NUM_TILES):
        if rng.random() < 0.12:
            delay.set_vn0(src, rng.randrange(60, 200))
        elif rng.random() < 0.25:
            delay.set_vn0(src, rng.randrange(8, 40))
        else:
            delay.set_vn0(src, rng.randrange(0, 4))
    # Forwards and responses get medium holds. These widen the window a
    # directory spends in S_D waiting for an owner's data, which is what a Put
    # has to land inside to reach the S_D rows of the table at all.
    for vnet in (1, 2, 3):
        for src in range(NUM_TILES):
            if rng.random() < 0.10:
                delay.set(vnet, src, rng.randrange(8, 120))
            else:
                delay.set(vnet, src, rng.randrange(0, 4))


async def _stress(dut, drv, delay, cov, addrs, target, racing=False,
                  store_prob=0.5, max_cycles=4_000_000):
    gauge = LivenessGauge(dut, every=8, mshr_bound=MSHR_TO, tbe_bound=TBE_TO)
    tracer = hangdump.Tracer(dut) if _HANG else None
    drv.racing = racing
    sets = sorted({sc.set_of(a) for a in addrs})
    checker = SwmrChecker(dut, sets)
    dut.dbg_set_i.value = checker.current_set()
    rng = drv.rng

    cyc = 0
    while drv.completed < target and cyc < max_cycles:
        if drv.outstanding() < 24:
            drv.plan(addrs, store_prob=store_prob)
        drv.drive()
        await step(dut)
        drv.cycle += 1
        cyc += 1
        drv._collect()
        cov.sample(drv.cycle)
        gauge.sample()
        checker.check(cyc)
        if _HANG:
            tracer.sample(cyc)
            if cyc % 64 == 0:
                who = hangdump.stuck(dut, _HANG)
                if who:
                    raise AssertionError(
                        hangdump.dump(dut, cyc, who)
                        + "\n  recent messages:\n" + tracer.text())
        if drv.mismatches:
            break
        if cyc % 700 == 0:
            _reshuffle(delay, rng)

    delay.clear()
    for _ in range(20_000):
        if drv.outstanding() == 0:
            break
        drv.drive()
        await step(dut)
        drv.cycle += 1
        drv._collect()
        cov.sample(drv.cycle)
        checker.check(cyc)

    drv.assert_clean()
    checker.assert_clean()
    assert drv.completed >= target, (
        f"only {drv.completed} of {target} requests completed in {cyc} cycles")
    return (f"{drv.completed} requests in {cyc} cycles; {checker.summary()}; "
            f"{gauge.summary()}")


@cocotb.test()
async def test_stress_4_lines(dut):
    """A footprint small enough that the two-way L1 thrashes on every access."""
    drv, delay, cov = await _setup(dut, seed=0x5EED0004)
    dut._log.info("4-line footprint: %s",
                  await _stress(dut, drv, delay, cov, footprint(4), REQUESTS,
                                store_prob=0.55))


@cocotb.test()
async def test_stress_16_lines(dut):
    drv, delay, cov = await _setup(dut, seed=0x5EED0016)
    dut._log.info("16-line footprint: %s",
                  await _stress(dut, drv, delay, cov, footprint(16), REQUESTS))


@cocotb.test()
async def test_stress_256_lines(dut):
    """Large enough to miss and fill rather than to conflict."""
    drv, delay, cov = await _setup(dut, seed=0x5EED0256)
    dut._log.info("256-line footprint: %s",
                  await _stress(dut, drv, delay, cov, footprint(256), REQUESTS,
                                store_prob=0.4))


def l2_pressure_footprint() -> list:
    """Twelve lines per L2 set, in every bank: the L2 is eight-way, so the
    ninth line in a set forces a back-invalidation and the directory has to
    recall lines the caches still hold.

    A footprint spread over many sets never does this -- the L2 is sixteen
    times the size of an L1 and simply absorbs it -- so without a run shaped
    like this the whole inclusion path is dead code as far as the stress tier
    is concerned.
    """
    addrs = []
    for tag in range(12):
        for bank in range(NUM_TILES):
            base = ru.addr_of(bank=bank, set_=0, tag=tag)
            addrs.extend([base, base + 8])
    assert max(addrs) < MEM_LINES * 32
    return addrs


@cocotb.test()
async def test_stress_l2_capacity_pressure(dut):
    """Sustained back-invalidation: more lines per L2 set than the L2 has ways.

    Every line here also lands in one L1 set, so both levels thrash at once --
    the L1 evicting under the directory's feet while the directory recalls
    lines out from under the L1.
    """
    drv, delay, cov = await _setup(dut, seed=0x5EED0002)
    dut._log.info("L2 capacity pressure: %s",
                  await _stress(dut, drv, delay, cov, l2_pressure_footprint(),
                                REQUESTS // 2, store_prob=0.5))


@cocotb.test()
async def test_stress_racing_same_line(dut):
    """Several cores in flight on one line at once.

    No response has a predictable value here, so the golden model steps back
    and the SWMR monitor, the design's assertions and a plausibility check on
    every load do the work. This is the configuration that reaches the
    transient-state forward arcs.
    """
    drv, delay, cov = await _setup(dut, seed=0x5EEDFACE)
    dut._log.info("racing, 4 lines: %s",
                  await _stress(dut, drv, delay, cov, footprint(4),
                                REQUESTS // 2, racing=True, store_prob=0.5))


@cocotb.test()
async def test_functional_coverage(dut):
    """Every legal bin exercised, or named and argued for.

    Runs last so it sees every bin the four runs above filled. A non-empty
    uncovered list fails: the specification is explicit that it is an open
    item, not a pass with a footnote.
    """
    global _COV
    assert _COV is not None, "coverage was never collected"
    report, open_items = _COV.report()
    for line in report.splitlines():
        dut._log.info("%s", line)
    assert not open_items, (
        f"{len(open_items)} uncovered legal bin(s):\n  "
        + "\n  ".join(f"{g}: {b}" for g, b in open_items))


@pytest.mark.stress
def test_stress():
    run(
        toplevel="system_top",
        test_module="test_stress",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LAT": MEM_LAT, "ENABLE_E": 1,
                    "TBE_TO": TBE_TO, "MSHR_TO": MSHR_TO},
    )
