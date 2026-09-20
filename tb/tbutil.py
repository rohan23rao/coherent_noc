"""Small helpers shared by every testbench tier."""

from cocotb.triggers import RisingEdge, Timer


def u(sig) -> int:
    """Read a signal as an unsigned int.

    In cocotb 2.x a 1-bit signal's ``.value`` is a ``Logic`` while a multi-bit
    signal's is a ``LogicArray``; only the latter has ``.to_unsigned()``.
    ``int()`` covers both, so every testbench goes through this one helper
    rather than guessing the width at each call site.
    """
    return int(sig.value)


async def step(dut, settle_ns: int = 1):
    """Advance one clock cycle and land just *after* the rising edge.

    This exists because of a cocotb scheduling detail that silently corrupts
    naive testbenches: a value written with ``sig.value = x`` is applied
    *after* the rising edge that is awaited next, so the DUT does not sample it
    at that edge but at the one after. Writing ``drive(); await RisingEdge()``
    therefore applies the stimulus a cycle later than it reads, which shows up
    as duplicated or dropped beats rather than as an obvious error.

    Using ``step()`` gives ordinary cycle semantics instead:

        await step(dut)   # just past an edge; registered outputs are settled
        sample(...)       # results of the stimulus driven last cycle
        drive(...)        # will be sampled at the next edge

    The small delay after the edge is a scheduling offset, not a settle-time
    hack -- no amount of it can hide a race, and none of the protocol tests are
    permitted to add cycles to make a race go away.
    """
    await RisingEdge(dut.clk)
    await Timer(settle_ns, "ns")


async def reset_dut(dut, cycles: int = 3, drive: dict | None = None):
    """Hold rst_n low for `cycles`, driving `drive` defaults onto the inputs."""
    for name, value in (drive or {}).items():
        getattr(dut, name).value = value
    dut.rst_n.value = 0
    for _ in range(cycles):
        await step(dut)
    dut.rst_n.value = 1
    await step(dut)
