"""Exhaustive check of the L1 coherence table (Phase 6).

Every (state, event) pair in l1_coh_fsm.sv is compared against an independent
Python transcription of the table in SPEC.md. Two transcriptions of the same
table, made separately, must agree -- that is a real check on the RTL, not a
tautology, because a transcription slip in one is very unlikely to be mirrored
in the other.

It also pins the blank cells: every pair the specification leaves blank must
raise `illegal`, and every pair it fills must not. A permissive default in the
RTL would show up here immediately.
"""

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import Timer

from runner import RTL_DIR, run
from tbutil import u

L1 = RTL_DIR / "l1"

from models.tables import (A, ACTION_FIELDS, EVENT_NAMES, STALL,
                           STATE_NAMES, L1_TABLE as TABLE,
                           I, S, E, M, IS_D, IM_AD, IM_A, SM_AD,
                           SM_A, MI_A, EI_A, SI_A, II_A,
                           LOAD, STORE, EVICT, FWD_GETS,
                           FWD_GETM, INV, PUT_ACK, DATA_E,
                           DATA_A0, DATA_AGT0, DATA_OWNER,
                           INV_ACK)



def _decode_actions(word: int) -> set:
    """Unpack l1_action_t. First-declared field is the MSB."""
    n = len(ACTION_FIELDS)
    out = set()
    for i, name in enumerate(ACTION_FIELDS):
        bit = n - 1 - i
        if (word >> bit) & 1:
            out.add(name)
    return out


@cocotb.test()
async def test_every_cell_matches_the_specification(dut):
    """All 13 x 12 cells, filled and blank alike."""
    checked = 0
    filled = 0
    blank = 0

    for st in range(13):
        for ev in range(12):
            dut.state_i.value = st
            dut.event_i.value = ev
            await Timer(1, "ns")

            acts = _decode_actions(u(dut.action_o))
            nxt = u(dut.next_state_o)
            key = (st, ev)
            where = f"{STATE_NAMES[st]} x {EVENT_NAMES[ev]}"

            if key in TABLE:
                want_next, want_acts = TABLE[key]
                assert "illegal" not in acts, (
                    f"{where}: RTL marks this cell illegal, but the "
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
                    f"{where}: the specification leaves this cell blank, but "
                    f"the RTL handles it as {sorted(acts)} -> "
                    f"{STATE_NAMES[nxt]}. A blank cell must raise an error, "
                    f"not be absorbed by a permissive default."
                )
                blank += 1
            checked += 1

    dut._log.info(
        "L1 table: %d cells checked, %d defined, %d correctly blank",
        checked, filled, blank,
    )
    assert checked == 13 * 12


@cocotb.test()
async def test_the_four_load_bearing_arcs(dut):
    """The four arcs the specification calls out, checked by name.

    These are duplicated from the exhaustive test on purpose. If someone
    "simplifies" the table, the exhaustive test says a cell changed; this one
    says which protocol property was broken.
    """
    async def cell(st, ev):
        dut.state_i.value = st
        dut.event_i.value = ev
        await Timer(1, "ns")
        return u(dut.next_state_o), _decode_actions(u(dut.action_o))

    nxt, acts = await cell(E, FWD_GETS)
    assert "send_data_dir" in acts and "send_data_req" in acts and nxt == S, (
        "E + Fwd-GetS must send data to the requester AND the directory: the "
        "directory cannot tell whether this core silently upgraded E->M."
    )

    nxt, acts = await cell(SM_AD, INV)
    assert acts == {"send_inv_ack"} and nxt == IM_AD, (
        "SM_AD + Inv must ack and fall back to IM_AD. Not acking deadlocks the "
        "core that sent the GetM; staying in SM_AD would expect only an "
        "AckCount when full data is now required."
    )

    nxt, acts = await cell(IM_AD, INV_ACK)
    assert acts == {"ack_dec"} and nxt == IM_AD, (
        "IM_AD + Inv-Ack must decrement and stay. Acks race ahead of the data, "
        "so the count goes negative -- which is why it is signed."
    )

    nxt, acts = await cell(MI_A, FWD_GETM)
    assert nxt == II_A and "send_data_req" in acts, (
        "MI_A + Fwd-GetM must go to II_A, not I. The PutM is still in flight "
        "and its Put-Ack is still owed."
    )

    dut._log.info("the four load-bearing arcs are intact")


@cocotb.test()
async def test_r8_forward_into_a_dead_mshr_is_impossible(dut):
    """II_A + Fwd-* must be illegal, not handled.

    Race R8. The directory removes a requester from the sharer list before
    sending its Put-Ack, so it cannot generate a forward for a core already in
    II_A. Adding an arc here would paper over a directory ordering bug; the
    correct response is to prove it cannot happen and assert it.
    """
    for ev, name in ((FWD_GETS, "Fwd-GetS"), (FWD_GETM, "Fwd-GetM")):
        dut.state_i.value = II_A
        dut.event_i.value = ev
        await Timer(1, "ns")
        acts = _decode_actions(u(dut.action_o))
        assert "illegal" in acts, (
            f"II_A + {name} is handled by the RTL. It must be illegal: if it "
            f"ever occurs the directory sent a forward after a Put-Ack, which "
            f"is a directory bug and must be fixed there."
        )
    dut._log.info("R8: forwards into II_A are marked impossible, as required")


@pytest.mark.protocol
def test_l1_table():
    run(
        toplevel="l1_coh_fsm",
        test_module="test_protocol_l1_table",
        sources=[L1 / "l1_coh_fsm.sv"],
    )
