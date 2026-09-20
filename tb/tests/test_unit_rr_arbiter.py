"""Unit tests for rtl/lib/rr_arbiter.sv.

What these prove:
  * a grant is always one-hot and always goes to an actual requester;
  * the pointer advances only when the grant is consumed, so a requester that
    is offered a grant it cannot take is not walked past;
  * under all-requesting traffic the service distribution is exactly uniform,
    measured over 10k cycles rather than asserted by inspection.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from runner import lib, run
from tbutil import reset_dut, step, u

N = 4
FAIRNESS_CYCLES = 10_000


async def _reset(dut):
    await reset_dut(dut, drive={"req_i": 0, "take_i": 0})


def _onehot_index(value: int) -> int:
    assert value != 0 and (value & (value - 1)) == 0, f"not one-hot: {value:#b}"
    return value.bit_length() - 1


@cocotb.test()
async def test_grant_is_onehot_and_legal(dut):
    """Every grant is one-hot and lands on a bit that was actually requesting."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _reset(dut)

    rng = random.Random(0xA5A5)
    for _ in range(2000):
        req = rng.randrange(0, 1 << N)
        dut.req_i.value = req
        dut.take_i.value = rng.randrange(2)
        await step(dut)

        gnt = u(dut.gnt_o)
        valid = u(dut.gnt_valid_o)

        if req == 0:
            assert gnt == 0, f"grant {gnt:#b} with no requests"
            assert valid == 0
        else:
            assert valid == 1, f"requests {req:#b} but gnt_valid low"
            assert gnt & req == gnt, f"granted non-requester: gnt={gnt:#b} req={req:#b}"
            idx = _onehot_index(gnt)
            assert u(dut.gnt_idx_o) == idx


@cocotb.test()
async def test_pointer_holds_when_grant_not_taken(dut):
    """With take_i low the winner must not change: no rotation without a grant.

    This is the starvation bug the mask/rotate style exists to avoid. If the
    pointer advanced on every cycle with a request, a requester whose grant is
    never consumed would be skipped on the next cycle and could be starved
    indefinitely under sustained backpressure.
    """
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _reset(dut)

    dut.req_i.value = 0b1111
    dut.take_i.value = 0
    await step(dut)
    first = u(dut.gnt_o)

    for cycle in range(20):
        await step(dut)
        now = u(dut.gnt_o)
        assert now == first, (
            f"pointer rotated with take_i low: cycle {cycle}, "
            f"grant moved {first:#b} -> {now:#b}"
        )


@cocotb.test()
async def test_round_robin_order_when_taken(dut):
    """With all four requesting and every grant taken, service is strict RR."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _reset(dut)

    dut.req_i.value = 0b1111
    dut.take_i.value = 1
    await step(dut)

    order = []
    for _ in range(4 * 3):
        order.append(_onehot_index(u(dut.gnt_o)))
        await step(dut)

    start = order[0]
    expected = [(start + i) % N for i in range(len(order))]
    assert order == expected, f"not round-robin: got {order}, expected {expected}"


@cocotb.test()
async def test_fairness_over_10k_cycles(dut):
    """Measured service distribution under saturation, reported not assumed."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _reset(dut)

    dut.req_i.value = (1 << N) - 1
    dut.take_i.value = 1
    await step(dut)

    counts = [0] * N
    for _ in range(FAIRNESS_CYCLES):
        counts[_onehot_index(u(dut.gnt_o))] += 1
        await step(dut)

    total = sum(counts)
    ideal = total / N
    spread = (max(counts) - min(counts)) / ideal

    dut._log.info("rr_arbiter fairness over %d cycles, all %d inputs requesting:", total, N)
    for i, c in enumerate(counts):
        dut._log.info("  input %d: %6d grants (%.4f of ideal)", i, c, c / ideal)
    dut._log.info("  max-min spread: %.6f of ideal share", spread)

    assert spread <= 1.0 / ideal + 1e-9, (
        f"round-robin under saturation must be exactly uniform to within one "
        f"grant; counts={counts}"
    )


@cocotb.test()
async def test_fairness_with_partial_requesters(dut):
    """Two of four requesting: each must get half, and the idle two get nothing."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _reset(dut)

    dut.req_i.value = 0b0101
    dut.take_i.value = 1
    await step(dut)

    counts = [0] * N
    for _ in range(FAIRNESS_CYCLES):
        counts[_onehot_index(u(dut.gnt_o))] += 1
        await step(dut)

    dut._log.info("partial-requester distribution: %s", counts)
    assert counts[1] == 0 and counts[3] == 0, f"granted a non-requester: {counts}"
    assert abs(counts[0] - counts[2]) <= 1, f"uneven split between active pair: {counts}"


@pytest.mark.unit
def test_rr_arbiter():
    run(
        toplevel="rr_arbiter",
        test_module="test_unit_rr_arbiter",
        sources=[lib("rr_arbiter")],
        parameters={"N": N},
    )
