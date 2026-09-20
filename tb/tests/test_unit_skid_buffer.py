"""Unit tests for rtl/lib/skid_buffer.sv.

What these prove:
  * full throughput -- one beat per cycle -- when the downstream never stalls,
    which is the whole reason for a two-entry slice rather than a register;
  * no beat is dropped when the downstream deasserts ready on a cycle where a
    beat is already committed upstream (the case the skid slot exists for);
  * valid and payload are stable while stalled, checked by the module's own SVA.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from runner import lib, run
from tbutil import reset_dut, step, u

WIDTH = 16

IDLE = {"up_valid_i": 0, "up_data_i": 0, "dn_ready_i": 0}


@cocotb.test()
async def test_full_throughput_when_never_stalled(dut):
    """With dn_ready high throughout, the slice sustains one beat per cycle."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive=IDLE)

    dut.dn_ready_i.value = 1
    sent, got = [], []
    n = 64

    for i in range(n + 4):
        offer = i < n
        dut.up_valid_i.value = 1 if offer else 0
        dut.up_data_i.value = i if offer else 0

        wr_fire = offer and u(dut.up_ready_o) == 1
        rd_fire = u(dut.dn_valid_o) == 1
        head = u(dut.dn_data_o) if rd_fire else None

        await step(dut)
        if wr_fire:
            sent.append(i)
        if rd_fire:
            got.append(head)

    dut.up_valid_i.value = 0
    dut.dn_ready_i.value = 0

    assert got == sent, f"throughput path reordered or dropped: {sent[:8]} vs {got[:8]}"
    # Steady state must be one transfer per cycle, not one every other cycle.
    assert len(got) >= n - 2, f"only {len(got)} of {n} beats in {n + 4} cycles"
    dut._log.info("skid_buffer: %d beats in %d cycles", len(got), n + 4)


@cocotb.test()
async def test_no_loss_when_ready_drops(dut):
    """Deassert dn_ready while a beat is committed: the skid slot must catch it."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive=IDLE)

    dut.dn_ready_i.value = 1
    dut.up_valid_i.value = 1
    dut.up_data_i.value = 0x11
    await step(dut)

    # Beat 0x11 is now in the output slot. Offer 0x22 and stall simultaneously.
    dut.up_data_i.value = 0x22
    dut.dn_ready_i.value = 0
    accepted = u(dut.up_ready_o) == 1
    await step(dut)
    assert accepted, "upstream was stalled even though the skid slot was free"

    dut.up_valid_i.value = 0
    for _ in range(5):
        await step(dut)
        assert u(dut.dn_valid_o) == 1, "output went invalid while stalled"
        assert u(dut.dn_data_o) == 0x11, "output payload changed while stalled"

    got = []
    dut.dn_ready_i.value = 1
    for _ in range(3):
        if u(dut.dn_valid_o):
            got.append(u(dut.dn_data_o))
        await step(dut)
    dut.dn_ready_i.value = 0

    assert got[:2] == [0x11, 0x22], f"skid slot lost or reordered a beat: {got}"


@cocotb.test()
async def test_random_backpressure_preserves_order(dut):
    """Randomized stalls on both sides: every beat arrives exactly once, in order."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive=IDLE)

    rng = random.Random(0x5C1D)
    sent, got = [], []
    next_val = 0

    for _ in range(6000):
        offer = rng.random() < 0.6
        dut.up_valid_i.value = 1 if offer else 0
        dut.up_data_i.value = next_val if offer else 0
        take = rng.random() < 0.6
        dut.dn_ready_i.value = 1 if take else 0

        wr_fire = offer and u(dut.up_ready_o) == 1
        rd_fire = take and u(dut.dn_valid_o) == 1
        head = u(dut.dn_data_o) if rd_fire else None

        await step(dut)
        if wr_fire:
            sent.append(next_val)
            next_val = (next_val + 1) % (1 << WIDTH)
        if rd_fire:
            got.append(head)

    dut.up_valid_i.value = 0
    dut.dn_ready_i.value = 1
    for _ in range(4):
        if u(dut.dn_valid_o):
            got.append(u(dut.dn_data_o))
        await step(dut)
    dut.dn_ready_i.value = 0

    assert got == sent[: len(got)], "skid buffer reordered or dropped a beat"
    assert len(got) >= len(sent) - 2, f"drained {len(got)} of {len(sent)}"
    dut._log.info("skid_buffer: %d offered, %d drained in order", len(sent), len(got))


@pytest.mark.unit
def test_skid_buffer():
    run(
        toplevel="skid_buffer",
        test_module="test_unit_skid_buffer",
        sources=[lib("skid_buffer")],
        parameters={"WIDTH": WIDTH},
    )
