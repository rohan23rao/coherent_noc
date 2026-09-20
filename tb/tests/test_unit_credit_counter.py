"""Unit tests for rtl/lib/credit_counter.sv.

What these prove:
  * the counter starts at DEPTH so the first DEPTH flits may launch immediately;
  * a simultaneous send and credit-return holds the count, rather than racing;
  * over randomized traffic the RTL tracks a Python model exactly, and the
    module's own SVA (no send on zero, no under/overflow) never fires.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from runner import lib, run
from tbutil import reset_dut, step, u

DEPTH = 4


@cocotb.test()
async def test_starts_full(dut):
    """After reset the counter holds DEPTH credits."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive={"send_i": 0, "credit_ret_i": 0})
    assert u(dut.credits_o) == DEPTH, f"reset value {u(dut.credits_o)} != {DEPTH}"
    assert u(dut.has_credit_o) == 1


@cocotb.test()
async def test_drain_and_refill(dut):
    """DEPTH sends empty it; DEPTH returns refill it."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive={"send_i": 0, "credit_ret_i": 0})

    for expect in range(DEPTH - 1, -1, -1):
        dut.send_i.value = 1
        await step(dut)
        dut.send_i.value = 0
        assert u(dut.credits_o) == expect, f"after send: {u(dut.credits_o)} != {expect}"

    assert u(dut.has_credit_o) == 0, "has_credit still asserted at zero"

    for expect in range(1, DEPTH + 1):
        dut.credit_ret_i.value = 1
        await step(dut)
        dut.credit_ret_i.value = 0
        assert u(dut.credits_o) == expect, f"after return: {u(dut.credits_o)} != {expect}"


@cocotb.test()
async def test_simultaneous_send_and_return_holds(dut):
    """send_i and credit_ret_i together must hold the count, not race.

    Treating the two as independent increments is the classic off-by-one that
    only shows up under sustained full-rate traffic -- the case a directed test
    is least likely to cover.
    """
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive={"send_i": 0, "credit_ret_i": 0})

    dut.send_i.value = 1
    await step(dut)
    dut.send_i.value = 0
    before = u(dut.credits_o)

    for _ in range(20):
        dut.send_i.value = 1
        dut.credit_ret_i.value = 1
        await step(dut)
        assert u(dut.credits_o) == before, (
            f"count moved on a simultaneous send+return: {before} -> {u(dut.credits_o)}"
        )
    dut.send_i.value = 0
    dut.credit_ret_i.value = 0


@cocotb.test()
async def test_random_traffic_matches_model(dut):
    """Randomized sends and returns, checked against a Python model each cycle."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive={"send_i": 0, "credit_ret_i": 0})

    rng = random.Random(0xC0FFEE)
    model = DEPTH
    outstanding = 0

    for cycle in range(5000):
        # Only legal stimulus: never send without credit, never return a credit
        # that was not consumed. Illegal stimulus would trip the module's own
        # assertions, which is a testbench bug, not a design finding.
        send = 1 if (model > 0 and rng.random() < 0.5) else 0
        ret = 1 if (outstanding > 0 and rng.random() < 0.5) else 0

        dut.send_i.value = send
        dut.credit_ret_i.value = ret
        await step(dut)

        model += ret - send
        outstanding += send - ret

        assert u(dut.credits_o) == model, (
            f"cycle {cycle}: rtl={u(dut.credits_o)} model={model} "
            f"(send={send} ret={ret})"
        )
        assert 0 <= model <= DEPTH


@pytest.mark.unit
def test_credit_counter():
    run(
        toplevel="credit_counter",
        test_module="test_unit_credit_counter",
        sources=[lib("credit_counter")],
        parameters={"DEPTH": DEPTH},
    )
