"""Exhaustive check of the directory table (Phase 6), in MSI and MESI modes.

Same method as the L1 table test: an independent Python transcription of the
table in SPEC.md, compared cell by cell against the RTL, with blank cells
required to raise `illegal`.

Running it in both MSI and MESI modes also pins the claim that enabling E
changes exactly one arc. If a second cell differs between the two modes, the
E-state delta is not what the documentation says it is.
"""

import cocotb
import pytest
from cocotb.triggers import Timer

from runner import RTL_DIR, run
from tbutil import u

L2 = RTL_DIR / "l2"

# coh_pkg::dir_state_e
DI, DS, DE, DM, SD = range(5)
STATE_NAMES = ["I", "S", "E", "M", "S_D"]

# coh_pkg::dir_event_e
(GETS, GETM, PUTS_NOT_LAST, PUTS_LAST, PUTM_OWNER, PUTM_NON_OWNER,
 PUTE_OWNER, PUTE_NON_OWNER, DATA) = range(9)
EVENT_NAMES = ["GetS", "GetM", "PutS(not last)", "PutS(last)",
               "PutM from owner", "PutM non-owner", "PutE from owner",
               "PutE non-owner", "Data"]

ACTION_FIELDS = [
    "illegal", "stall", "send_data", "send_data_e", "send_inv_others",
    "send_fwd_gets", "send_fwd_getm", "send_put_ack", "add_sharer",
    "remove_sharer", "clear_sharers", "sharers_owner_req", "set_owner_req",
    "clear_owner", "copy_data_l2",
]

ACK = {"send_put_ack"}
REMOVE_ACK = {"remove_sharer", "send_put_ack"}


def _table(enable_e: bool) -> dict:
    t = {
        (DI, GETM):            (DM, {"send_data", "set_owner_req"}),
        (DI, PUTS_NOT_LAST):   (DI, ACK),
        (DI, PUTS_LAST):       (DI, ACK),
        (DI, PUTM_NON_OWNER):  (DI, ACK),
        (DI, PUTE_NON_OWNER):  (DI, ACK),

        (DS, GETS):            (DS, {"send_data", "add_sharer"}),
        (DS, GETM):            (DM, {"send_data", "send_inv_others",
                                     "clear_sharers", "set_owner_req"}),
        (DS, PUTS_NOT_LAST):   (DS, REMOVE_ACK),
        (DS, PUTS_LAST):       (DI, REMOVE_ACK),
        (DS, PUTM_NON_OWNER):  (DS, REMOVE_ACK),
        (DS, PUTE_NON_OWNER):  (DS, REMOVE_ACK),

        (DE, GETS):            (SD, {"send_fwd_gets", "sharers_owner_req",
                                     "clear_owner"}),
        (DE, GETM):            (DM, {"send_fwd_getm", "set_owner_req"}),
        (DE, PUTS_NOT_LAST):   (DE, ACK),
        (DE, PUTS_LAST):       (DE, ACK),
        (DE, PUTM_OWNER):      (DI, {"copy_data_l2", "send_put_ack", "clear_owner"}),
        (DE, PUTM_NON_OWNER):  (DE, ACK),
        (DE, PUTE_OWNER):      (DI, {"send_put_ack", "clear_owner"}),
        (DE, PUTE_NON_OWNER):  (DE, ACK),

        (DM, GETS):            (SD, {"send_fwd_gets", "sharers_owner_req",
                                     "clear_owner"}),
        (DM, GETM):            (DM, {"send_fwd_getm", "set_owner_req"}),
        (DM, PUTS_NOT_LAST):   (DM, ACK),
        (DM, PUTS_LAST):       (DM, ACK),
        (DM, PUTM_OWNER):      (DI, {"copy_data_l2", "send_put_ack", "clear_owner"}),
        (DM, PUTM_NON_OWNER):  (DM, ACK),
        (DM, PUTE_OWNER):      (DM, ACK),
        (DM, PUTE_NON_OWNER):  (DM, ACK),

        (SD, GETS):            (SD, {"stall"}),
        (SD, GETM):            (SD, {"stall"}),
        (SD, PUTS_NOT_LAST):   (SD, REMOVE_ACK),
        (SD, PUTS_LAST):       (SD, REMOVE_ACK),
        (SD, PUTM_OWNER):      (SD, REMOVE_ACK),
        (SD, PUTM_NON_OWNER):  (SD, REMOVE_ACK),
        (SD, PUTE_OWNER):      (SD, REMOVE_ACK),
        (SD, PUTE_NON_OWNER):  (SD, REMOVE_ACK),
        (SD, DATA):            (DS, {"copy_data_l2"}),
    }
    # The single arc that E changes.
    if enable_e:
        t[(DI, GETS)] = (DE, {"send_data_e", "set_owner_req"})
    else:
        t[(DI, GETS)] = (DS, {"send_data", "add_sharer"})
    return t


def _decode(word: int) -> set:
    n = len(ACTION_FIELDS)
    return {name for i, name in enumerate(ACTION_FIELDS) if (word >> (n - 1 - i)) & 1}


async def _cell(dut, enable_e, st, ev):
    dut.enable_e_i.value = 1 if enable_e else 0
    dut.state_i.value = st
    dut.event_i.value = ev
    await Timer(1, "ns")
    return u(dut.next_state_o), _decode(u(dut.action_o))


@cocotb.test()
async def test_every_cell_matches_the_specification(dut):
    """All 5 x 9 cells, in both MSI and MESI modes."""
    for enable_e in (False, True):
        table = _table(enable_e)
        mode = "MESI" if enable_e else "MSI"
        filled = blank = 0
        for st in range(5):
            for ev in range(9):
                nxt, acts = await _cell(dut, enable_e, st, ev)
                where = f"[{mode}] {STATE_NAMES[st]} x {EVENT_NAMES[ev]}"
                if (st, ev) in table:
                    want_next, want_acts = table[(st, ev)]
                    assert "illegal" not in acts, (
                        f"{where}: RTL marks this cell illegal; the "
                        f"specification defines it as {sorted(want_acts)}"
                    )
                    assert nxt == want_next, (
                        f"{where}: RTL goes to {STATE_NAMES[nxt]}, "
                        f"specification says {STATE_NAMES[want_next]}"
                    )
                    assert acts == want_acts, (
                        f"{where}: RTL actions {sorted(acts)}, "
                        f"specification says {sorted(want_acts)}"
                    )
                    filled += 1
                else:
                    assert "illegal" in acts, (
                        f"{where}: the specification leaves this cell blank, "
                        f"but the RTL handles it as {sorted(acts)}"
                    )
                    blank += 1
        dut._log.info("directory table (%s): %d defined, %d correctly blank",
                      mode, filled, blank)


@cocotb.test()
async def test_enabling_e_changes_exactly_one_arc(dut):
    """The MSI/MESI delta must be I + GetS and nothing else.

    The documentation claims adding E is a one-arc change. This measures it
    rather than asserting it: if a second cell differs, the claim is wrong and
    the Phase 6 to Phase 7 step is not the clean isolation it is presented as.
    """
    differing = []
    for st in range(5):
        for ev in range(9):
            msi = await _cell(dut, False, st, ev)
            mesi = await _cell(dut, True, st, ev)
            if msi != mesi:
                differing.append((STATE_NAMES[st], EVENT_NAMES[ev]))

    assert differing == [("I", "GetS")], (
        f"enabling E changed {len(differing)} arcs, not one: {differing}"
    )
    dut._log.info("MSI -> MESI delta is exactly one arc: I + GetS")


@cocotb.test()
async def test_s_d_stalls_requests_but_accepts_every_put(dut):
    """The arc that keeps S_D from deadlocking.

    Stalling Puts in S_D would deadlock: the cache that owes this directory its
    data may itself be blocked waiting for a Put-Ack from here.
    """
    for ev in (GETS, GETM):
        _, acts = await _cell(dut, True, SD, ev)
        assert acts == {"stall"}, f"S_D must stall {EVENT_NAMES[ev]}, got {sorted(acts)}"

    for ev in (PUTS_NOT_LAST, PUTS_LAST, PUTM_OWNER, PUTM_NON_OWNER,
               PUTE_OWNER, PUTE_NON_OWNER):
        _, acts = await _cell(dut, True, SD, ev)
        assert "stall" not in acts and "send_put_ack" in acts, (
            f"S_D must accept and ack {EVENT_NAMES[ev]}; stalling it deadlocks"
        )
    dut._log.info("S_D stalls GetS/GetM and acks all six Put forms")


@cocotb.test()
async def test_put_from_non_owner_never_clears_the_owner(dut):
    """Race R5: a late PutE from a previous owner must not disown the new one."""
    for st in (DE, DM):
        for ev in (PUTM_NON_OWNER, PUTE_NON_OWNER):
            nxt, acts = await _cell(dut, True, st, ev)
            assert "clear_owner" not in acts, (
                f"{STATE_NAMES[st]} x {EVENT_NAMES[ev]} clears the owner. "
                f"A stale Put from a core that no longer owns the line would "
                f"disown the current owner and lose its data."
            )
            assert nxt == st, (
                f"{STATE_NAMES[st]} x {EVENT_NAMES[ev]} changed state to "
                f"{STATE_NAMES[nxt]}; a stale Put must change nothing"
            )
    dut._log.info("R5: a Put from a non-owner never clears the owner")


@pytest.mark.protocol
def test_dir_table():
    run(
        toplevel="dir_coh_fsm",
        test_module="test_protocol_dir_table",
        sources=[L2 / "dir_coh_fsm.sv"],
    )
