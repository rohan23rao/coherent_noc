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


def s(sig) -> int:
    """Read a signal as a SIGNED int, using the signal's own width.

    The width is deliberately not written down here. A testbench constant that
    mirrors an RTL parameter is the same defect as bug B22, one layer out: when
    ``ACK_CNT_W`` was derived from ``NUM_TILES`` the probe's hard-coded ``4``
    stopped matching the 3-bit signal it was decoding, read a stored -2 as +6,
    and reported that race R1 had not happened. The assertion was right and the
    instrument was wrong, which is the worse way round.

    ``LogicArray.to_signed()`` asks the handle, so there is nothing to keep in
    step. A 1-bit ``Logic`` has no such method and cannot be negative anyway.
    """
    v = sig.value
    to_signed = getattr(v, "to_signed", None)
    return to_signed() if to_signed is not None else int(v)


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


async def reset_dut(dut, cycles: int = 3, drive: dict | None = None,
                    rst_name: str = "rst_n"):
    """Hold the reset low for `cycles`, driving `drive` defaults onto the inputs.

    `rst_name` selects which reset port to drive: testbench harnesses take a
    synchronous `rst_n` directly, while system_top takes the raw asynchronous
    `arst_n` and synchronizes it internally -- which is the point of having a
    single synchronizer in the top.
    """
    for name, value in (drive or {}).items():
        getattr(dut, name).value = value
    rst = getattr(dut, rst_name)
    rst.value = 0
    for _ in range(cycles):
        await step(dut)
    rst.value = 1
    for _ in range(cycles):
        await step(dut)
