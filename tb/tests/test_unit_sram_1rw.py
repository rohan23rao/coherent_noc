"""Unit tests for rtl/lib/sram_1rw.sv.

What these prove the port contract actually is, since every cache pipeline in
the design is built against it:
  * read latency is exactly one cycle -- rdata_o is valid the cycle after
    en_i && !we_i, not the same cycle;
  * writes land and read back;
  * rdata_o holds its previous value across a write cycle, so no consumer may
    infer anything about the data being written;
  * en_i low is a true no-op on both the array and the output register.
"""

import random

import cocotb
import pytest
from cocotb.clock import Clock

from runner import lib, run
from tbutil import step, u

WIDTH = 32
DEPTH = 64


async def _write(dut, addr: int, data: int):
    dut.en_i.value = 1
    dut.we_i.value = 1
    dut.addr_i.value = addr
    dut.wdata_i.value = data
    await step(dut)
    dut.en_i.value = 0
    dut.we_i.value = 0


async def _read(dut, addr: int) -> int:
    """Issue a read and return the data one cycle later, per the contract."""
    dut.en_i.value = 1
    dut.we_i.value = 0
    dut.addr_i.value = addr
    await step(dut)
    dut.en_i.value = 0
    return u(dut.rdata_o)


@cocotb.test()
async def test_write_then_read(dut):
    """Data written is the data read back."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.en_i.value = 0
    dut.we_i.value = 0
    dut.addr_i.value = 0
    dut.wdata_i.value = 0
    await step(dut)

    for addr, data in [(0, 0xDEADBEEF), (1, 0x00C0FFEE), (DEPTH - 1, 0xFFFFFFFF)]:
        await _write(dut, addr, data)
    for addr, data in [(0, 0xDEADBEEF), (1, 0x00C0FFEE), (DEPTH - 1, 0xFFFFFFFF)]:
        got = await _read(dut, addr)
        assert got == data, f"addr {addr}: read {got:#x}, wrote {data:#x}"


@cocotb.test()
async def test_read_latency_is_exactly_one_cycle(dut):
    """rdata_o is NOT valid in the same cycle as the read request."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.en_i.value = 0
    dut.we_i.value = 0
    await step(dut)

    await _write(dut, 5, 0x12345678)
    await _write(dut, 6, 0xABCDEF01)

    # Prime the output register with the contents of address 5.
    got5 = await _read(dut, 5)
    assert got5 == 0x12345678

    # Now request address 6. In the cycle the request is presented, rdata_o must
    # still show address 5's data; the new data appears the cycle after.
    dut.en_i.value = 1
    dut.we_i.value = 0
    dut.addr_i.value = 6
    same_cycle = u(dut.rdata_o)
    assert same_cycle == 0x12345678, (
        f"read data appeared too early: got {same_cycle:#x} in the request cycle, "
        "before the edge that performs the read. The pipelines are designed "
        "around a one-cycle latency."
    )
    await step(dut)
    dut.en_i.value = 0
    assert u(dut.rdata_o) == 0xABCDEF01, "read data did not appear after one cycle"


@cocotb.test()
async def test_rdata_holds_across_a_write(dut):
    """A write cycle must not disturb the read data register."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.en_i.value = 0
    dut.we_i.value = 0
    await step(dut)

    await _write(dut, 9, 0x0BADF00D)
    held = await _read(dut, 9)
    assert held == 0x0BADF00D

    await _write(dut, 10, 0x99999999)
    assert u(dut.rdata_o) == held, (
        f"rdata_o changed during a write: {u(dut.rdata_o):#x} != {held:#x}. "
        "There is no read-during-write forwarding on this port."
    )


@cocotb.test()
async def test_disabled_is_a_noop(dut):
    """en_i low touches neither the array nor the output register."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.en_i.value = 0
    dut.we_i.value = 0
    await step(dut)

    await _write(dut, 3, 0x5A5A5A5A)
    primed = await _read(dut, 3)
    assert primed == 0x5A5A5A5A

    # Wiggle the inputs with en_i low; nothing may change.
    dut.en_i.value = 0
    dut.we_i.value = 1
    dut.addr_i.value = 3
    dut.wdata_i.value = 0xFFFFFFFF
    for _ in range(4):
        await step(dut)
    dut.we_i.value = 0
    assert u(dut.rdata_o) == primed, "output register moved with en_i low"

    again = await _read(dut, 3)
    assert again == 0x5A5A5A5A, f"array was written with en_i low: {again:#x}"


@cocotb.test()
async def test_random_write_read_matches_model(dut):
    """Randomized traffic against a Python dict model."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.en_i.value = 0
    dut.we_i.value = 0
    await step(dut)

    rng = random.Random(0x512A)
    model = {}

    for _ in range(1500):
        addr = rng.randrange(DEPTH)
        if rng.random() < 0.5:
            data = rng.randrange(1 << WIDTH)
            await _write(dut, addr, data)
            model[addr] = data
        elif addr in model:
            got = await _read(dut, addr)
            assert got == model[addr], (
                f"addr {addr}: read {got:#x}, model {model[addr]:#x}"
            )

    dut._log.info("sram_1rw: %d addresses written and verified", len(model))


@pytest.mark.unit
def test_sram_1rw():
    run(
        toplevel="sram_1rw",
        test_module="test_unit_sram_1rw",
        sources=[lib("sram_1rw")],
        parameters={"WIDTH": WIDTH, "DEPTH": DEPTH},
    )
