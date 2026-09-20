"""MESI scenarios, written once and run against two configurations.

The same bodies run on the direct-connect harness (Phase 6/7) and on the full
system over the 2x2 mesh (Phase 8). That is the point: if a scenario passes
directly and fails over the network, the network is the suspect, and the two
runs share a body so the comparison is exact rather than approximate.
"""

from models.coherence_checker import E, I, M, S, STATE_NAMES, SwmrChecker
from models.golden import line_addr
from models.multicore import NUM_TILES, OP_LD, OP_ST
from tbutil import step, u

STRIDE = 1 << 13


def l1_of(dut, tile: int):
    """The L1 instance, whichever top we are running under.

    The direct-connect harness puts the cache at gen_tile[t].u_l1; the full
    system wraps it in a tile, at gen_tile[t].u_tile.u_l1.
    """
    node = dut.gen_tile[tile]
    return node.u_tile.u_l1 if hasattr(node, "u_tile") else node.u_l1


def set_of(addr: int) -> int:
    return (addr >> 7) & 63


def tag_of(addr: int) -> int:
    return ((addr >> 13) << 2) | ((addr >> 5) & 0x3)


def state_of(dut, tile: int, addr: int) -> int:
    sw = u(dut.dbg_state_o)
    tw = u(dut.dbg_tag_o)
    want = tag_of(addr)
    for w in range(2):
        st = (sw >> ((tile * 2 + w) * 4)) & 0xF
        tg = (tw >> ((tile * 2 + w) * 21)) & ((1 << 21) - 1)
        if st != I and tg == want:
            return st
    return I


async def do_op(drv, tile, op, addr, wdata=0, be=0xF, timeout=20000,
                probe=None):
    dut = drv.dut
    ln = line_addr(addr)
    tag = min(drv.free_tags[tile])
    want = drv.gold.store(addr, wdata, be) if op == OP_ST else drv.gold.load(addr)
    drv.free_tags[tile].discard(tag)
    drv.busy_lines.add(ln)
    drv.expected[tile][tag] = want
    drv.ctx[tile][tag] = f"{'ST' if op else 'LD'} {addr:#x}"
    drv.inflight_line[tile][tag] = ln
    drv.offer[tile] = (tag, op, addr, wdata, be)
    for _ in range(timeout):
        drv.drive()
        await step(dut)
        drv.cycle += 1
        drv._collect()
        if probe is not None:
            probe.sample(drv.cycle)
        if tag not in drv.pending[tile] and drv.offer[tile] is None:
            drv.idle()
            drv.assert_clean()
            return want
    raise AssertionError(
        f"tile {tile} {'ST' if op else 'LD'} {addr:#x} never completed"
    )


async def scenario_lone_reader_gets_e(dut, drv, addr=0x0400):
    dut.dbg_set_i.value = set_of(addr)
    await do_op(drv, 0, OP_LD, addr)
    for _ in range(8):
        await step(dut)
    st = state_of(dut, 0, addr)
    assert st == E, (
        f"a lone reader ended in {STATE_NAMES[st]}, expected E -- without this "
        f"a read-then-write costs two transactions instead of one"
    )
    return "lone reader got E"


async def scenario_silent_upgrade(dut, drv, addr=0x0480):
    dut.dbg_set_i.value = set_of(addr)
    await do_op(drv, 1, OP_LD, addr)
    assert state_of(dut, 1, addr) == E, "setup: tile 1 should hold the line in E"

    tag = min(drv.free_tags[1])
    drv.free_tags[1].discard(tag)
    drv.expected[1][tag] = drv.gold.store(addr, 0x5151A5A5)
    drv.ctx[1][tag] = "ST"
    drv.inflight_line[1][tag] = line_addr(addr)
    drv.offer[1] = (tag, OP_ST, addr, 0x5151A5A5, 0xF)

    sent = 0
    for _ in range(400):
        drv.drive()
        sent += u(l1_of(dut, 1).vn0_valid_o)
        await step(dut)
        drv._collect()
        if tag not in drv.pending[1] and drv.offer[1] is None:
            break
    drv.idle()
    drv.assert_clean()

    assert sent == 0, f"the E->M upgrade sent {sent} VN0 message(s); it must be silent"
    assert state_of(dut, 1, addr) == M, "the line should now be M"
    return "E->M upgrade was silent: zero VN0 messages"


async def scenario_two_cores_share(dut, drv, addr=0x0440):
    dut.dbg_set_i.value = set_of(addr)
    await do_op(drv, 0, OP_LD, addr)
    await do_op(drv, 1, OP_LD, addr)
    for _ in range(8):
        await step(dut)
    s0, s1 = state_of(dut, 0, addr), state_of(dut, 1, addr)
    assert s0 == S and s1 == S, (
        f"expected both cores in S, got {STATE_NAMES[s0]} and {STATE_NAMES[s1]}"
    )
    return "two cores share a line: both in S"


async def scenario_store_invalidates(dut, drv, addr=0x04C0):
    dut.dbg_set_i.value = set_of(addr)
    await do_op(drv, 0, OP_LD, addr)
    await do_op(drv, 1, OP_LD, addr)
    await do_op(drv, 2, OP_ST, addr, wdata=0xA5A5A5A5)
    for _ in range(16):
        await step(dut)
    states = [state_of(dut, t, addr) for t in range(NUM_TILES)]
    writers = [t for t, s in enumerate(states) if s in (M, E)]
    readers = [t for t, s in enumerate(states) if s == S]
    assert writers == [2], (
        f"expected only tile 2 to hold the line for write, got "
        f"{[(t, STATE_NAMES[s]) for t, s in enumerate(states)]}"
    )
    assert not readers, "sharers survived the store"
    got = await do_op(drv, 2, OP_LD, addr)
    assert got == 0xA5A5A5A5
    return "store invalidated both sharers; tile 2 alone in M"


async def scenario_dirty_read_downgrades(dut, drv, addr=0x0580):
    dut.dbg_set_i.value = set_of(addr)
    await do_op(drv, 0, OP_ST, addr, wdata=0xDEC0DE01)
    got = await do_op(drv, 2, OP_LD, addr)
    assert got == 0xDEC0DE01, (
        f"reader got {got:#x}, not the owner's dirty value -- Fwd-GetS did not "
        f"deliver the data"
    )
    for _ in range(16):
        await step(dut)
    assert state_of(dut, 0, addr) == S, "the owner should have downgraded to S"
    assert state_of(dut, 2, addr) == S, "the reader should be in S"
    return "dirty line read: owner downgraded to S, reader in S with the data"


async def scenario_r11(dut, drv, a0=0x0600):
    """E, silent upgrade to M, then PutM while the directory still records E."""
    a1, a2 = a0 + STRIDE, a0 + 2 * STRIDE
    dut.dbg_set_i.value = set_of(a0)

    await do_op(drv, 0, OP_LD, a0)
    assert state_of(dut, 0, a0) == E, "setup: tile 0 should hold a0 in E"
    await do_op(drv, 0, OP_ST, a0, wdata=0x11DEAD11)
    assert state_of(dut, 0, a0) == M
    await do_op(drv, 0, OP_LD, a1)
    await do_op(drv, 0, OP_LD, a2)
    for _ in range(40):
        await step(dut)

    got = await do_op(drv, 1, OP_LD, a0)
    assert got == 0x11DEAD11, (
        f"tile 1 read {got:#x}, expected 0x11DEAD11. The directory was in E and "
        f"got a PutM carrying data it did not expect; dropping it loses the "
        f"silent upgrade's store."
    )
    return "R11: silent E->M survived eviction through an unexpected PutM"


async def scenario_r5(dut, drv, delay, a0=0x0700, hold=120):
    """A Put that lands after ownership has moved must not disown the new owner."""
    a1 = a0 + STRIDE
    dut.dbg_set_i.value = set_of(a0)

    await do_op(drv, 0, OP_LD, a0)
    assert state_of(dut, 0, a0) == E, "setup: tile 0 should hold a0 in E"

    delay.set_vn0(0, hold)

    tag = min(drv.free_tags[0])
    drv.free_tags[0].discard(tag)
    drv.expected[0][tag] = drv.gold.load(a1)
    drv.ctx[0][tag] = "LD a1"
    drv.inflight_line[0][tag] = line_addr(a1)
    drv.offer[0] = (tag, OP_LD, a1, 0, 0xF)
    for _ in range(14):
        drv.drive()
        await step(dut)
        drv._collect()
    drv.idle()

    await do_op(drv, 1, OP_ST, a0, wdata=0x5A5A0001)
    assert state_of(dut, 1, a0) == M, "tile 1 should own a0 for writing"

    delay.clear()
    for _ in range(600):
        drv.drive()
        await step(dut)
        drv._collect()
    drv.idle()
    drv.assert_clean()

    got = await do_op(drv, 1, OP_LD, a0)
    assert got == 0x5A5A0001, (
        f"tile 1 read back {got:#x}, expected 0x5A5A0001. A stale Put from the "
        f"previous owner disowned the current one and its store was lost."
    )
    got2 = await do_op(drv, 2, OP_LD, a0)
    assert got2 == 0x5A5A0001, (
        f"tile 2 read {got2:#x}; the line the directory hands out no longer "
        f"matches the owner's data"
    )
    return "R5: a stale Put from the previous owner did not disown the new one"


async def scenario_random(dut, drv, target=10_000, max_cycles=1_500_000,
                          mem_lines=1024):
    addrs = []
    for s in range(8):
        for tconf in range(2):
            base = (s * 128) + (tconf << 13)
            addrs.extend([base, base + 4])
    addrs = [a for a in addrs if a < mem_lines * 32]
    sets = sorted({set_of(a) for a in addrs})

    checker = SwmrChecker(dut, sets)
    dut.dbg_set_i.value = checker.current_set()

    for cyc in range(max_cycles):
        if drv.completed >= target:
            break
        if drv.outstanding() < 24:
            drv.plan(addrs, store_prob=0.45)
        drv.drive()
        await step(dut)
        drv.cycle += 1
        drv._collect()
        checker.check(cyc)
        if drv.mismatches:
            break

    drv.assert_clean()
    checker.assert_clean()
    assert drv.completed >= target, (
        f"only {drv.completed} of {target} completed in {drv.cycle} cycles"
    )
    return (f"{drv.completed} requests over {drv.cycle} cycles; {checker.summary()}")
