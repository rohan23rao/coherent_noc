"""2x2 mesh tests (Phase 3).

What these prove:
  * every injected packet is ejected exactly once, at the addressed tile, with
    its flits in order and no foreign flit spliced into it;
  * this holds under uniform, tornado and hot-spot traffic, which stress
    different parts of the network -- uniform spreads load, tornado maximises
    the distance every packet travels, and hot-spot deliberately oversubscribes
    one ejection port;
  * nothing is ever routed off the edge of the mesh (asserted in RTL);
  * the load-latency curve, measured and written to docs/noc_perf.md.
"""

import json
import random
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock

from models.noc_tb import (
    NUM_TILES,
    PATTERNS,
    MeshHarness,
    dest_uniform_no_self,
    hops,
    tile_xy,
)
from runner import REPO_ROOT, RTL_DIR, lib, run
from tbutil import reset_dut, u

NOC = RTL_DIR / "noc"
PERF_JSON = REPO_ROOT / "sim" / "noc_perf.json"


def _sources():
    return [
        lib("rr_arbiter"), lib("credit_counter"), lib("fifo"),
        NOC / "route_compute.sv", NOC / "input_unit.sv",
        NOC / "vc_allocator.sv", NOC / "switch_allocator.sv",
        NOC / "crossbar.sv", NOC / "router.sv", NOC / "noc_top.sv",
    ]


async def _setup(dut, credit_delay=1):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    h = MeshHarness(dut, credit_delay=credit_delay)
    h.idle()
    await reset_dut(dut, drive={})
    return h


def _check_delivery(h, expect):
    """Every expected flit ejected exactly once, at the right tile, in order."""
    seen = {}
    per_packet = {}
    for tile, flit, cyc in h.ejected:
        assert flit.payload in expect, (
            f"tile {tile} ejected unknown payload {flit.payload:#x}"
        )
        assert flit.payload not in seen, (
            f"payload {flit.payload:#x} ejected twice "
            f"(tiles {seen[flit.payload]} and {tile})"
        )
        seen[flit.payload] = tile
        want_tile = expect[flit.payload]
        assert tile == want_tile, (
            f"payload {flit.payload:#x} ejected at tile {tile}, addressed to {want_tile}"
        )
        per_packet.setdefault((flit.payload >> 12), []).append(flit)

    missing = set(expect) - set(seen)
    assert not missing, f"{len(missing)} flits never ejected, e.g. {sorted(missing)[:4]}"

    for pid, flits in per_packet.items():
        payloads = [f.payload for f in flits]
        assert payloads == sorted(payloads), f"packet {pid} ejected out of order: {payloads}"
        assert flits[0].head == 1 and flits[-1].tail == 1, f"packet {pid} head/tail wrong"
        assert sum(f.head for f in flits) == 1, f"packet {pid} has two heads"
    return per_packet


async def _run_pattern(dut, name, n_packets=60, max_cycles=20000):
    h = await _setup(dut)
    rng = random.Random(0xBEEF ^ hash(name) & 0xFFFF)
    chooser = PATTERNS[name]

    expect = {}
    for _ in range(n_packets):
        src = rng.randrange(NUM_TILES)
        dst = chooser(rng, src)
        n = rng.choice([1, 1, 3])
        base = h.generate(src, dst, n, vnet=rng.randrange(3))
        for i in range(n):
            expect[base + i] = dst

    for _ in range(max_cycles):
        await h.tick()
        if h.backlog() == 0 and len(h.ejected) >= len(expect):
            for _ in range(8):
                await h.tick()
            break
    else:
        raise AssertionError(
            f"{name}: timed out with {len(h.ejected)}/{len(expect)} flits ejected"
        )

    _check_delivery(h, expect)
    avg_lat = sum(h.latencies) / len(h.latencies)
    dut._log.info(
        "%s: %d packets / %d flits delivered, mean packet latency %.1f cycles",
        name, n_packets, len(expect), avg_lat,
    )
    return h


@cocotb.test()
async def test_uniform_traffic(dut):
    """Uniform random destinations, excluding self."""
    await _run_pattern(dut, "uniform")


@cocotb.test()
async def test_tornado_traffic(dut):
    """Every packet to (x + X/2) mod X -- the maximum-distance X permutation."""
    await _run_pattern(dut, "tornado")


@cocotb.test()
async def test_hotspot_traffic(dut):
    """All four tiles to tile 3, deliberately oversubscribing one eject port.

    Tile 3 is where the memory controller hangs in the full system, so this is
    not a synthetic worst case -- it is the traffic the coherence phases will
    actually produce when every directory misses to memory at once.
    """
    await _run_pattern(dut, "hotspot")


@cocotb.test()
async def test_no_packet_leaves_the_mesh(dut):
    """Sustained all-to-all with the RTL edge assertions active.

    noc_top asserts that no flit is ever presented on an edge port and that an
    ejected flit is addressed to the tile ejecting it. This test exists to drive
    enough traffic past those assertions to mean something.
    """
    h = await _setup(dut)
    rng = random.Random(0x3D6E)
    expect = {}
    for _ in range(150):
        src = rng.randrange(NUM_TILES)
        dst = rng.randrange(NUM_TILES)
        n = rng.choice([1, 2, 3])
        base = h.generate(src, dst, n, vnet=rng.randrange(3))
        for i in range(n):
            expect[base + i] = dst

    for _ in range(30000):
        await h.tick()
        if h.backlog() == 0 and len(h.ejected) >= len(expect):
            for _ in range(8):
                await h.tick()
            break
    else:
        raise AssertionError(f"timed out: {len(h.ejected)}/{len(expect)}")

    _check_delivery(h, expect)
    dut._log.info("edge assertions survived %d flits over %d cycles", len(expect), h.cycle)


@cocotb.test()
async def test_load_latency_curve(dut):
    """Sweep offered load and record mean latency, for docs/noc_perf.md.

    Single-flit packets so that offered load in flits/cycle/node equals the
    injection probability, which is what makes the x-axis meaningful.
    """
    results = []
    for rate in [0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70]:
        h = await _setup(dut)
        rng = random.Random(0xA11 + int(rate * 1000))

        warmup, measure, drain = 200, 2000, 3000
        total_hops = 0
        n_gen = 0
        for cyc in range(warmup + measure):
            for src in range(NUM_TILES):
                if rng.random() < rate:
                    dst = dest_uniform_no_self(rng, src)
                    h.generate(src, dst, 1, vnet=rng.randrange(3))
                    total_hops += hops(src, dst)
                    n_gen += 1
            await h.tick()

        for _ in range(drain):
            await h.tick()
            if h.backlog() == 0 and len(h.latencies) >= n_gen:
                break

        completed = len(h.latencies)
        mean_lat = sum(h.latencies) / completed if completed else float("nan")
        # Accepted throughput in flits per cycle per node.
        accepted = completed / (h.cycle * NUM_TILES)
        results.append(
            {
                "offered": rate,
                "accepted": accepted,
                "mean_latency": mean_lat,
                "completed": completed,
                "generated": n_gen,
                "mean_hops": total_hops / n_gen if n_gen else 0.0,
            }
        )
        dut._log.info(
            "load %.2f: accepted %.3f flits/cyc/node, mean latency %.1f cycles (%d/%d done)",
            rate, accepted, mean_lat, completed, n_gen,
        )

    PERF_JSON.parent.mkdir(parents=True, exist_ok=True)
    PERF_JSON.write_text(json.dumps(results, indent=2))
    dut._log.info("load-latency data written to %s", PERF_JSON)

    # Zero-load latency must be the pipeline depth plus hops, not something wild.
    assert results[0]["mean_latency"] < 40, (
        f"unloaded latency {results[0]['mean_latency']:.1f} is implausibly high"
    )
    # Latency must increase monotonically enough to show a knee.
    assert results[-1]["mean_latency"] > results[0]["mean_latency"], (
        "latency did not grow with offered load -- the sweep is not loading the network"
    )


@cocotb.test()
async def test_credit_round_trip_limits_throughput_not_correctness(dut):
    """Stretch the credit round trip past VC_DEPTH and watch throughput fall.

    This is the experiment behind the standard claim that
    `buffer depth >= bandwidth x credit round-trip latency` is a THROUGHPUT
    condition and not a correctness one. Increasing the downstream credit
    return delay lengthens the round trip without changing anything else. If
    the rule were about correctness, the long-delay runs would lose or corrupt
    flits; instead they deliver every flit and simply take longer, because a VC
    sits idle waiting for credits it has already earned.

    The router's own credit assertions -- no send on zero credits, no buffer
    overflow, counter bounds -- are active in every run, so a correctness
    failure would be caught rather than inferred from the throughput number.
    """
    measured = []
    for delay in [1, 4, 8, 16]:
        h = await _setup(dut, credit_delay=delay)
        rng = random.Random(0xC5D7 + delay)
        n_gen = 0
        for _ in range(1500):
            for src in range(NUM_TILES):
                if rng.random() < 0.30:
                    h.generate(src, dest_uniform_no_self(rng, src), 1, vnet=rng.randrange(3))
                    n_gen += 1
            await h.tick()
        for _ in range(8000):
            await h.tick()
            if h.backlog() == 0 and len(h.latencies) >= n_gen:
                break

        completed = len(h.latencies)
        assert completed == n_gen, (
            f"credit_delay={delay}: {n_gen - completed} packets lost. A longer "
            f"credit round trip must cost throughput, never delivery."
        )
        thr = completed / (h.cycle * NUM_TILES)
        lat = sum(h.latencies) / completed
        measured.append({"credit_delay": delay, "accepted": thr, "mean_latency": lat})
        dut._log.info(
            "credit_delay=%2d: all %d packets delivered, accepted %.3f flits/cyc/node, mean latency %.1f",
            delay, completed, thr, lat,
        )

    path = PERF_JSON.parent / "noc_credit_sweep.json"
    path.write_text(json.dumps(measured, indent=2))

    # Correctness held at every delay (asserted above). Throughput must degrade.
    assert measured[-1]["accepted"] < measured[0]["accepted"], (
        "stretching the credit round trip did not reduce throughput -- the "
        "sweep is not actually loading the network"
    )


@pytest.mark.noc
def test_mesh():
    run(
        toplevel="noc_top",
        test_module="test_noc_mesh",
        sources=_sources(),
    )
