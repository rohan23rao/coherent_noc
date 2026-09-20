"""Standalone router tests (Phase 2).

What these prove:
  * the flit codec agrees with coh_pkg::flit_t bit-for-bit, verified against the
    DUT rather than assumed;
  * every input port reaches every output port XY routing permits, and never
    reaches one it forbids;
  * a multi-flit packet leaves in order, on one VC, with no other packet's flits
    interleaved into it;
  * nothing is lost or duplicated under randomized all-to-all traffic;
  * credit backpressure throttles rather than drops, and every in-RTL credit and
    buffer assertion stays quiet throughout.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from models.flit import Flit
from models.noc_tb import (
    PORT_EAST,
    PORT_LOCAL,
    PORT_NAMES,
    PORT_NORTH,
    PORT_SOUTH,
    PORT_WEST,
    RouterHarness,
)
from runner import RTL_DIR, lib, run
from tbutil import reset_dut, step, u

NOC = RTL_DIR / "noc"


def _sources():
    return [
        lib("rr_arbiter"),
        lib("credit_counter"),
        lib("fifo"),
        NOC / "route_compute.sv",
        NOC / "input_unit.sv",
        NOC / "vc_allocator.sv",
        NOC / "switch_allocator.sv",
        NOC / "crossbar.sv",
        NOC / "router.sv",
    ]


def _expected_port(my_x: int, my_y: int, dst_x: int, dst_y: int) -> int:
    """The XY route the RTL should compute. Kept independent of the RTL."""
    if dst_x != my_x:
        return PORT_EAST if dst_x > my_x else PORT_WEST
    if dst_y != my_y:
        return PORT_SOUTH if dst_y > my_y else PORT_NORTH
    return PORT_LOCAL


def _packet(dst_x, dst_y, src_id, n_flits, payload_base):
    flits = []
    for i in range(n_flits):
        flits.append(
            Flit(
                head=1 if i == 0 else 0,
                tail=1 if i == n_flits - 1 else 0,
                dst_x=dst_x,
                dst_y=dst_y,
                src_id=src_id,
                payload=payload_base + i,
            )
        )
    return flits


async def _setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    h = RouterHarness(dut)
    h.idle()
    await reset_dut(dut, drive={})
    return h


@cocotb.test()
async def test_flit_codec_matches_rtl(dut):
    """A flit injected with distinctive fields emerges with all of them intact.

    This is the test that pins the packed-struct bit order. If head/tail or the
    port-array indexing were reversed, every later test would still "pass" on
    symmetric payloads and fail unpredictably on real traffic.
    """
    h = await _setup(dut)
    my_x = int(dut.MY_X.value)
    my_y = int(dut.MY_Y.value)

    # Choose a destination that is definitely not this router.
    dst_x, dst_y = (1 - my_x), my_y
    flits = _packet(dst_x, dst_y, src_id=3, n_flits=1, payload_base=0x0123456789ABCDEF)
    h.queue_packet(PORT_LOCAL, flits, vnet=2)

    assert await h.drain(), "packet never came back out"
    assert len(h.received) == 1, f"expected 1 flit out, got {len(h.received)}"

    out_port, got, _ = h.received[0]
    assert out_port == _expected_port(my_x, my_y, dst_x, dst_y)
    assert got.head == 1 and got.tail == 1, f"head/tail corrupted: {got}"
    assert got.dst_x == dst_x and got.dst_y == dst_y, f"destination corrupted: {got}"
    assert got.src_id == 3, f"src_id corrupted: {got.src_id}"
    assert got.payload == 0x0123456789ABCDEF, f"payload corrupted: {got.payload:#x}"
    assert got.vnet == 2, f"vnet changed in flight: {got.vnet}"
    dut._log.info("flit codec verified against RTL at (%d,%d)", my_x, my_y)


@cocotb.test()
async def test_every_input_reaches_every_legal_output(dut):
    """Each input port routes to each destination XY allows, and no other."""
    h = await _setup(dut)
    my_x = int(dut.MY_X.value)
    my_y = int(dut.MY_Y.value)

    expect = {}
    tag = 0
    for in_port in range(5):
        for dst_x in range(2):
            for dst_y in range(2):
                out_port = _expected_port(my_x, my_y, dst_x, dst_y)
                tag += 1
                payload = (tag << 8) | (in_port << 4)
                h.queue_packet(
                    in_port,
                    _packet(dst_x, dst_y, src_id=in_port % 4, n_flits=1, payload_base=payload),
                    vnet=tag % 3,
                )
                expect[payload] = (in_port, out_port)

    assert await h.drain(), "not every packet drained"

    seen = {}
    for out_port, flit, _ in h.received:
        assert flit.payload in expect, f"unexpected payload {flit.payload:#x} out of port {out_port}"
        in_port, want = expect[flit.payload]
        assert out_port == want, (
            f"{PORT_NAMES[in_port]} -> {PORT_NAMES[out_port]} but XY says "
            f"{PORT_NAMES[want]} for dst=({flit.dst_x},{flit.dst_y})"
        )
        seen[flit.payload] = out_port

    assert len(seen) == len(expect), (
        f"{len(expect) - len(seen)} packets never arrived"
    )

    turns = sorted({(expect[p][0], o) for p, o in seen.items()})
    dut._log.info(
        "router at (%d,%d): %d input->output turns exercised: %s",
        my_x, my_y, len(turns),
        ", ".join(f"{PORT_NAMES[i]}->{PORT_NAMES[o]}" for i, o in turns),
    )


@cocotb.test()
async def test_multiflit_packet_stays_contiguous(dut):
    """Body flits follow their head, in order, with nothing interleaved."""
    h = await _setup(dut)
    my_x = int(dut.MY_X.value)
    my_y = int(dut.MY_Y.value)
    dst_x, dst_y = (1 - my_x), my_y

    # Two packets from different input ports to the same output, same vnet, so
    # they compete for that output port's VCs.
    h.queue_packet(PORT_LOCAL, _packet(dst_x, dst_y, 0, 3, 0x1000), vnet=0)
    h.queue_packet(PORT_NORTH, _packet(dst_x, dst_y, 1, 3, 0x2000), vnet=0)

    assert await h.drain(), "packets did not drain"

    out_port = _expected_port(my_x, my_y, dst_x, dst_y)
    stream = [f for p, f, _ in h.received if p == out_port]
    assert len(stream) == 6, f"expected 6 flits, got {len(stream)}"

    # Group by VC: each VC must carry one complete, contiguous, in-order packet.
    per_vc = {}
    for f in stream:
        per_vc.setdefault((f.vnet, f.vc_id), []).append(f)

    for vc, flits in per_vc.items():
        assert flits[0].head == 1, f"VC {vc} did not start with a head"
        assert flits[-1].tail == 1, f"VC {vc} did not end with a tail"
        assert sum(f.head for f in flits) == 1, f"VC {vc} interleaved two packets"
        assert sum(f.tail for f in flits) == 1, f"VC {vc} interleaved two packets"
        base = flits[0].payload
        assert [f.payload for f in flits] == [base + i for i in range(len(flits))], (
            f"VC {vc} reordered a packet: {[hex(f.payload) for f in flits]}"
        )
    dut._log.info("two 3-flit packets stayed contiguous on %d VCs", len(per_vc))


@cocotb.test()
async def test_random_traffic_no_loss_or_duplication(dut):
    """Randomized all-to-all: every flit out exactly once, correctly routed."""
    h = await _setup(dut)
    my_x = int(dut.MY_X.value)
    my_y = int(dut.MY_Y.value)

    rng = random.Random(0xB0A7)
    expect = {}
    payload = 1
    for _ in range(120):
        in_port = rng.randrange(5)
        dst_x, dst_y = rng.randrange(2), rng.randrange(2)
        n = rng.choice([1, 1, 2, 3])
        base = payload << 8
        payload += 1
        h.queue_packet(
            in_port,
            _packet(dst_x, dst_y, rng.randrange(4), n, base),
            vnet=rng.randrange(3),
        )
        for i in range(n):
            expect[base + i] = _expected_port(my_x, my_y, dst_x, dst_y)

    assert await h.drain(max_cycles=6000), (
        f"drain timed out: sent {len(h.sent)}, received {len(h.received)}"
    )

    got = {}
    for out_port, flit, _ in h.received:
        assert flit.payload not in got, f"flit {flit.payload:#x} emitted twice"
        got[flit.payload] = out_port

    missing = set(expect) - set(got)
    assert not missing, f"{len(missing)} flits lost, e.g. {sorted(missing)[:4]}"
    for pl, want in expect.items():
        assert got[pl] == want, f"flit {pl:#x} left by {PORT_NAMES[got[pl]]}, expected {PORT_NAMES[want]}"

    dut._log.info("random traffic: %d flits, none lost or duplicated", len(expect))


@cocotb.test()
async def test_slow_credit_return_throttles_but_never_drops(dut):
    """A downstream that returns credits slowly must throttle, not lose flits.

    This is the credit-starvation case: with credit_delay well above VC_DEPTH,
    the DUT spends most cycles unable to launch. The in-RTL assertions -- no
    send on zero credits, no VC buffer overflow, credit counter bounds -- are
    active throughout, so a credit accounting bug shows up as an assertion
    failure rather than as a silent stall.
    """
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    h = RouterHarness(dut, credit_delay=12)
    h.idle()
    await reset_dut(dut, drive={})

    my_x = int(dut.MY_X.value)
    my_y = int(dut.MY_Y.value)
    rng = random.Random(0x57A11)

    expect = {}
    payload = 1
    for _ in range(30):
        in_port = rng.randrange(5)
        dst_x, dst_y = rng.randrange(2), rng.randrange(2)
        n = rng.choice([1, 2])
        base = payload << 8
        payload += 1
        h.queue_packet(in_port, _packet(dst_x, dst_y, 0, n, base), vnet=rng.randrange(3))
        for i in range(n):
            expect[base + i] = _expected_port(my_x, my_y, dst_x, dst_y)

    assert await h.drain(max_cycles=12000), (
        f"drain timed out under slow credits: sent {len(h.sent)}, got {len(h.received)}"
    )

    got = {f.payload: p for p, f, _ in h.received}
    assert set(got) == set(expect), (
        f"{len(set(expect) - set(got))} flits lost under credit backpressure"
    )
    dut._log.info(
        "slow credits (delay=12): %d flits delivered over %d cycles",
        len(expect), h.cycle,
    )


@pytest.mark.noc
@pytest.mark.parametrize("my_x,my_y", [(0, 0), (1, 1)])
def test_router(my_x, my_y):
    """Run the router suite at two mesh corners.

    A 1-bit coordinate cannot express "further east" from x=1, so a single
    router in a 2x2 mesh can only ever drive three of its five output ports.
    (0,0) covers EAST/SOUTH/LOCAL and (1,1) covers WEST/NORTH/LOCAL; together
    they exercise all five outputs from all five inputs. The remaining
    input-to-output combinations are covered at mesh level in Phase 3.
    """
    run(
        toplevel="router",
        test_module="test_noc_router",
        sources=_sources(),
        parameters={"MY_X": my_x, "MY_Y": my_y},
    )
