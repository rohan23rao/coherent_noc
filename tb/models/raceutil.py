"""Cycle-level driving for the directed race tests.

`scenarios.do_op` issues one operation and waits for it, which is the right
shape for everything except a race: a race needs two operations *in flight at
once on the same line*, with a chosen order imposed by the network-delay hook
rather than by the testbench's issue order. These helpers do that, and nothing
else.

Rules this file exists to keep:

  * Nothing here waits "long enough". Every wait is either for a named
    condition (a state observed, a response collected) or for a hold that a
    test set deliberately, and a hold is part of the stimulus, not a settle.
  * An operation issued into a race has `expected=None`, so no per-response
    check is made; the outcome is checked afterwards from a quiesced machine,
    against values the test knows because it chose them.
  * The probe is sampled on every cycle these helpers advance, so a test can
    assert the interleaving happened and not merely that the answer was right.
"""

from models.golden import line_addr
from models.probe import l1_of
from models.multicore import NUM_TILES, OP_LD, OP_ST
from tbutil import step, u

MSHR_ENTRIES = 4
LINE_ADDR_W = 27
L1_STATE_W = 4

STRIDE_SET = 1 << 7      # next L1/L2 set, same bank
STRIDE_TAG = 1 << 13     # same set, same bank, different tag


def addr_of(bank: int, set_: int = 0, tag: int = 0, word: int = 0) -> int:
    """An address with a chosen home bank, cache set and tag.

    addr[4:0] offset, addr[6:5] home bank, addr[12:7] set index, addr[31:13]
    upper tag -- the decode from coh_pkg, spelled out so a race test can say
    "homed at tile 3, in the same set as the others" and mean it.
    """
    assert 0 <= bank < NUM_TILES and 0 <= set_ < 64 and 0 <= word < 8
    return (tag << 13) | (set_ << 7) | (bank << 5) | (word << 2)


def home_of(addr: int) -> int:
    return (addr >> 5) & 0x3


async def tick(dut, drv, probe=None, n: int = 1, gauge=None):
    """Advance n cycles, driving pending offers and collecting responses."""
    for _ in range(n):
        drv.drive()
        await step(dut)
        drv.cycle += 1
        drv._collect()
        if probe is not None:
            probe.sample(drv.cycle)
        if gauge is not None:
            gauge.sample()


def issue(drv, tile: int, op: int, addr: int, wdata: int = 0, be: int = 0xF,
          expect=None, note: str = ""):
    """Offer one operation to a core without predicting its response.

    Returns the tag so a caller can wait for exactly this operation.
    """
    assert drv.offer[tile] is None, f"tile {tile} already has an offer pending"
    tag = min(drv.free_tags[tile])
    drv.free_tags[tile].discard(tag)
    drv.expected[tile][tag] = expect
    drv.ctx[tile][tag] = note or f"{'ST' if op == OP_ST else 'LD'} {addr:#x}"
    drv.inflight_line[tile][tag] = line_addr(addr)
    drv.offer[tile] = (tag, op, addr, wdata, be)
    return tag


async def wait_done(dut, drv, tile: int, tag: int, probe=None,
                    max_cycles: int = 4000, what: str = ""):
    for _ in range(max_cycles):
        if tag not in drv.pending[tile] and drv.offer[tile] is None:
            return
        await tick(dut, drv, probe)
    raise AssertionError(
        f"tile {tile} tag {tag} ({what or 'operation'}) never completed in "
        f"{max_cycles} cycles"
    )


async def wait_quiet(dut, drv, probe=None, max_cycles: int = 8000, gauge=None):
    """Run until nothing is outstanding anywhere."""
    for _ in range(max_cycles):
        if drv.outstanding() == 0:
            return
        await tick(dut, drv, probe, gauge=gauge)
    raise AssertionError(
        f"{drv.outstanding()} operation(s) still outstanding after "
        f"{max_cycles} cycles"
    )


async def wait_for(dut, drv, cond, probe=None, max_cycles: int = 4000,
                   what: str = "condition"):
    """Advance until `cond()` is true. Raises if it never is.

    This is how a race test waits for the machine to reach the state the race
    starts from -- never with a fixed number of cycles chosen by experiment.
    """
    for _ in range(max_cycles):
        if cond():
            return
        await tick(dut, drv, probe)
    raise AssertionError(f"{what} never became true within {max_cycles} cycles")


def l1_state_in_mshr(dut, tile: int, addr: int):
    """The state the L1 would present to its table for a forward to `addr`.

    An evicting line's coherence state lives in its MSHR, not in the tag array,
    so the debug bus reports I for a line in MI_A -- and MI_A is exactly what
    races R3, R4 and R8 need to wait for. This reads what the controller reads.

    `mshr` is a packed array, so it arrives as one vector. Only the top three
    fields of `mshr_e` are decoded here -- valid, addr, state, in that order
    from the MSB -- which is why the widths of everything below them do not
    appear: the entry width is taken from the handle.
    """
    l1 = l1_of(dut, tile)
    total = len(l1.mshr)
    w = total // MSHR_ENTRIES
    raw = u(l1.mshr)
    la = line_addr(addr)
    for i in range(MSHR_ENTRIES):
        ent = (raw >> (i * w)) & ((1 << w) - 1)
        if not (ent >> (w - 1)) & 1:
            continue
        if ((ent >> (w - 1 - LINE_ADDR_W)) & ((1 << LINE_ADDR_W) - 1)) == la:
            return (ent >> (w - 1 - LINE_ADDR_W - L1_STATE_W)) & ((1 << L1_STATE_W) - 1)
    return None
