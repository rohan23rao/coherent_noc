"""Unit tests for rtl/lib/fifo.sv.

What these prove:
  * ordering is strict FIFO across randomized backpressure on both sides;
  * the full and empty flags are exact -- DEPTH entries fit, DEPTH+1 do not;
  * at full, a concurrent read does not let a write in during the same cycle,
    because wr_ready_o is independent of rd_ready_i by design;
  * no beat is ever lost or duplicated over a long randomized run.
"""

import random
from collections import deque

import cocotb
import pytest
from cocotb.clock import Clock

from runner import lib, run
from tbutil import reset_dut, step, u

WIDTH = 16
DEPTH = 4

IDLE = {"wr_valid_i": 0, "wr_data_i": 0, "rd_ready_i": 0}


@cocotb.test()
async def test_fills_to_depth_then_backpressures(dut):
    """Exactly DEPTH beats fit; the next one is refused."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive=IDLE)

    assert u(dut.empty_o) == 1 and u(dut.full_o) == 0

    for i in range(DEPTH):
        assert u(dut.wr_ready_o) == 1, f"refused beat {i} before reaching DEPTH"
        dut.wr_valid_i.value = 1
        dut.wr_data_i.value = 0xA0 + i
        await step(dut)
    dut.wr_valid_i.value = 0

    assert u(dut.full_o) == 1, "not full after DEPTH writes"
    assert u(dut.wr_ready_o) == 0, "still accepting writes while full"
    assert u(dut.count_o) == DEPTH

    # Drain and confirm order.
    got = []
    dut.rd_ready_i.value = 1
    for _ in range(DEPTH):
        assert u(dut.rd_valid_o) == 1
        # Show-ahead FIFO: rd_data_o is the head *now*; the step below pops it.
        got.append(u(dut.rd_data_o))
        await step(dut)
    dut.rd_ready_i.value = 0
    await step(dut)

    assert got == [0xA0 + i for i in range(DEPTH)], f"out of order: {got}"
    assert u(dut.empty_o) == 1, "not empty after draining everything"


@cocotb.test()
async def test_no_read_when_empty(dut):
    """rd_valid_o must stay low with nothing buffered, even with rd_ready high."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive=IDLE)

    dut.rd_ready_i.value = 1
    for _ in range(10):
        await step(dut)
        assert u(dut.rd_valid_o) == 0, "claimed valid data while empty"
    dut.rd_ready_i.value = 0


@cocotb.test()
async def test_full_refuses_write_even_with_concurrent_read(dut):
    """At full, a concurrent read does NOT open a slot in the same cycle.

    This is the module's contract, not an oversight: wr_ready_o is `!full_o` and
    deliberately does not depend on rd_ready_i. Making the write fit would mean
    `wr_ready_o = !full_o || do_rd`, which puts a combinational path from the
    downstream ready back to the upstream ready -- exactly the path a ready/valid
    cut exists to break.

    The cost is one cycle of re-acceptance latency on entry to full, and it is
    paid once: the write lands on the following cycle, and steady-state
    throughput with both sides active is still one beat per cycle. What it does
    mean is that under sustained full-duplex traffic the occupancy settles at
    DEPTH-1 rather than DEPTH, which is the number that matters when sizing VC
    buffers against the credit round trip in Phase 3.
    """
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive=IDLE)

    for i in range(DEPTH):
        dut.wr_valid_i.value = 1
        dut.wr_data_i.value = 0x10 + i
        await step(dut)
    dut.wr_valid_i.value = 0
    assert u(dut.full_o) == 1 and u(dut.wr_ready_o) == 0

    # Offer a write and a read together while full.
    dut.rd_ready_i.value = 1
    dut.wr_valid_i.value = 1
    dut.wr_data_i.value = 0xBB
    refused = u(dut.wr_ready_o) == 0
    await step(dut)
    assert refused, "wr_ready_o rose while full -- it must not depend on rd_ready_i"
    assert u(dut.count_o) == DEPTH - 1, (
        f"the read should have drained one slot: count={u(dut.count_o)}"
    )

    # Now the slot is free, so the same write is accepted on the next cycle.
    assert u(dut.wr_ready_o) == 1, "still refusing a write one cycle after draining"
    dut.rd_ready_i.value = 0
    await step(dut)
    dut.wr_valid_i.value = 0
    assert u(dut.count_o) == DEPTH, f"the deferred write did not land: {u(dut.count_o)}"

    # Nothing was lost: 0x10.. minus the one that was read, then 0xBB.
    got = []
    dut.rd_ready_i.value = 1
    while u(dut.rd_valid_o) == 1:
        got.append(u(dut.rd_data_o))
        await step(dut)
    dut.rd_ready_i.value = 0
    assert got == [0x11, 0x12, 0x13, 0xBB], f"data lost or reordered: {got}"


@cocotb.test()
async def test_random_backpressure_preserves_every_beat(dut):
    """Long randomized run: nothing lost, nothing duplicated, order preserved."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut, drive=IDLE)

    rng = random.Random(0xF1F0)
    model = deque()
    sent = []
    got = []
    next_val = 0

    for _ in range(6000):
        offer = rng.random() < 0.55
        take = rng.random() < 0.55

        dut.wr_valid_i.value = 1 if offer else 0
        dut.wr_data_i.value = next_val if offer else 0
        dut.rd_ready_i.value = 1 if take else 0

        wr_fire = offer and u(dut.wr_ready_o) == 1
        rd_fire = take and u(dut.rd_valid_o) == 1
        head = u(dut.rd_data_o) if rd_fire else None

        await step(dut)

        if rd_fire:
            expected = model.popleft()
            assert head == expected, f"head {head:#x} != model {expected:#x}"
            got.append(head)
        if wr_fire:
            model.append(next_val)
            sent.append(next_val)
            next_val = (next_val + 1) % (1 << WIDTH)

        assert u(dut.count_o) == len(model), (
            f"count {u(dut.count_o)} != model depth {len(model)}"
        )

    dut.wr_valid_i.value = 0
    dut.rd_ready_i.value = 1
    while u(dut.rd_valid_o) == 1:
        got.append(u(dut.rd_data_o))
        model.popleft()
        await step(dut)
    dut.rd_ready_i.value = 0

    assert got == sent[: len(got)], "FIFO reordered or dropped a beat"
    dut._log.info("fifo: %d beats written, %d read back in order", len(sent), len(got))


@pytest.mark.unit
def test_fifo():
    run(
        toplevel="fifo",
        test_module="test_unit_fifo",
        sources=[lib("fifo")],
        parameters={"WIDTH": WIDTH, "DEPTH": DEPTH},
    )
