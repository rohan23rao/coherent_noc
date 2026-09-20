"""Single-line RTL mutations, each paired with the test that must catch it.

This file is the answer to "how do you know your tests have teeth?". A test
that passes proves nothing on its own: it might be checking a value that is
right for a reason unrelated to the arc it is named after, or it might not be
reaching the arc at all. Deleting the arc and watching the test fail is the
only evidence that the test is doing work.

Each entry names the RTL it breaks, the exact text to replace, the test that
must then FAIL, and why that failure is the expected one. `scripts/mutate.py`
applies them one at a time, restores the file afterwards, and reports any
mutation that a test failed to notice -- a surviving mutant is an open item,
not a pass.

Rules for adding one:
  * It must break a real handling arc, not a syntax error or a tautology.
  * `find` must appear EXACTLY ONCE in the file; the runner checks.
  * The expectation is failure of one named test. If it also breaks others,
    that is fine and not recorded -- the claim is only that the named test is
    sensitive to this arc.
"""

L1FSM = "rtl/l1/l1_coh_fsm.sv"
DIRFSM = "rtl/l2/dir_coh_fsm.sv"
L1 = "rtl/l1/l1_cache.sv"
DIR = "rtl/l2/dir_ctrl.sv"
VCALLOC = "rtl/noc/vc_allocator.sv"

RACE_TESTS = "tests/test_races.py"

MUTATIONS = [
    dict(
        name="r1-drop-early-inv-ack",
        race="R1",
        file=L1FSM,
        why="Without a cell for IM_AD + Inv-Ack the table calls the event "
            "impossible, so an ack that overtakes its Data raises the illegal-"
            "transition assertion instead of being counted.",
        find="""          EV_INV_ACK: begin
            // *** The early-ack arc. An Inv-Ack arrived before the Data that
            // carries the AckCount, so the count goes NEGATIVE and is credited
            // back up when Data lands. This is why ack_cnt is signed.
            action_o = '0; action_o.ack_dec = 1'b1;
          end
""",
        replace="",
        test=RACE_TESTS,
        case="test_r1_early_inv_ack",
    ),
    dict(
        name="r1-unsigned-ack-cnt",
        race="R1",
        file=L1,
        why="The same race seen from the arithmetic: comparing the count as "
            "unsigned turns -2 into 14, so it never reaches zero and the "
            "transaction never completes.",
        find="""    if (((vn2_next == L1_IM_A) || (vn2_next == L1_SM_A)) && (vn2_ack_next == '0)) begin""",
        replace="""    if (((vn2_next == L1_IM_A) || (vn2_next == L1_SM_A)) && (vn2_ack_next == 4'd1)) begin""",
        test=RACE_TESTS,
        case="test_r1_early_inv_ack",
    ),
    dict(
        name="r2-drop-sm-ad-inv",
        race="R2",
        file=L1FSM,
        why="An upgrader that cannot answer an Inv leaves the core that won "
            "the race waiting for an ack that never comes.",
        find="""          EV_INV: begin
            // *** Lost the race: somebody else's GetM was ordered first and
            // took this core's shared copy. It MUST ack, or that core waits
            // forever -- and it must now expect full data, not just an
            // AckCount, so it falls back to IM_AD rather than staying here.
            action_o = '0; action_o.send_inv_ack = 1'b1;
            next_state_o = L1_IM_AD;
          end
""",
        replace="",
        test=RACE_TESTS,
        case="test_r2_upgrade_loses_the_race",
    ),
    dict(
        name="r3-drop-mi-a-fwd-getm",
        race="R3",
        file=L1FSM,
        why="A cache with a PutM in flight still owns the data. If it cannot "
            "answer a forward, the requester gets nothing.",
        find="""          EV_FWD_GETM: begin
            // *** Not I. The PutM is still in flight and its Put-Ack is still
            // owed; retiring the MSHR here would leave a message arriving with
            // no state to receive it.
            action_o = '0; action_o.send_data_req = 1'b1;
            next_state_o = L1_II_A;
          end
""",
        replace="",
        test=RACE_TESTS,
        case="test_r3_writeback_vs_forward",
    ),
    dict(
        name="r4-stale-puts-clears-owner",
        race="R4",
        file=DIRFSM,
        why="Treating a PutS as authoritative while in M disowns the current "
            "writer, and its acknowledged store is then lost to memory.",
        find="""          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST: begin
            action_o = '0; action_o.send_put_ack = 1'b1;
          end
          DEV_PUTM_OWNER: begin
            // Race R11: the owner had E, silently upgraded to M, and is now
            // evicting with data while the directory still recorded E. The
            // data must be taken.""",
        replace="""          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST: begin
            action_o = '0; action_o.send_put_ack = 1'b1;
            action_o.clear_owner = 1'b1;
            next_state_o = DIR_I;
          end
          DEV_PUTM_OWNER: begin
            // Race R11: the owner had E, silently upgraded to M, and is now
            // evicting with data while the directory still recorded E. The
            // data must be taken.""",
        test=RACE_TESTS,
        case="test_r4_stale_puts",
    ),
    dict(
        name="r5-drop-put-owner-check",
        race="R5",
        file=DIR,
        why="Without the owner check every Put looks like the owner's, so a "
            "late PutE from the previous owner clears the new owner.",
        find="""      MSG_PUTE:    dir_event = is_owner ? DEV_PUTE_OWNER : DEV_PUTE_NON_OWNER;""",
        replace="""      MSG_PUTE:    dir_event = DEV_PUTE_OWNER;""",
        test=RACE_TESTS,
        case="test_r5_pute_from_non_owner",
    ),
    dict(
        name="r6-drop-im-ad-fwd-stall",
        race="R6",
        file=L1FSM,
        why="A cache in IM_AD has no data to forward. Without the stall the "
            "table calls the second hand-off impossible.",
        find="""      L1_IM_AD: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT,
          EV_FWD_GETS, EV_FWD_GETM, EV_INV: begin
            action_o = '0; action_o.stall = 1'b1;
          end""",
        replace="""      L1_IM_AD: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT,
          EV_FWD_GETS, EV_INV: begin
            action_o = '0; action_o.stall = 1'b1;
          end""",
        test=RACE_TESTS,
        case="test_r6_two_getms_back_to_back",
    ),
    dict(
        name="r7-s-d-serves-gets",
        race="R7",
        file=DIRFSM,
        why="In S_D the L2 copy is stale by construction. Serving a reader "
            "from it returns a value the owner overwrote.",
        find="""          DEV_GETS, DEV_GETM: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST,
          DEV_PUTM_OWNER, DEV_PUTM_NON_OWNER,
          DEV_PUTE_OWNER, DEV_PUTE_NON_OWNER: begin""",
        replace="""          DEV_GETS: begin
            action_o = '0;
            action_o.send_data  = 1'b1;
            action_o.add_sharer = 1'b1;
          end
          DEV_GETM: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST,
          DEV_PUTM_OWNER, DEV_PUTM_NON_OWNER,
          DEV_PUTE_OWNER, DEV_PUTE_NON_OWNER: begin""",
        test=RACE_TESTS,
        case="test_r7_gets_into_s_d",
    ),
    dict(
        name="r8-owner-not-reassigned-on-forward",
        race="R8",
        file=DIRFSM,
        why="R8 is impossible only because the directory stops naming a cache "
            "the moment it forwards away from it -- the owner changes when the "
            "forward is SENT, not when the data lands. Leave the old owner in "
            "place and the next request is forwarded to a cache that has "
            "already given the line up and is sitting in II_A: exactly the "
            "forward into a dead MSHR the L1 asserts is impossible.",
        find="""          DEV_GETM: begin
            // Stays in M; the owner simply changes. This is how two
            // back-to-back GetMs are serialized (race R6).
            action_o = '0;
            action_o.send_fwd_getm = 1'b1;
            action_o.set_owner_req = 1'b1;
          end""",
        replace="""          DEV_GETM: begin
            // Stays in M; the owner simply changes. This is how two
            // back-to-back GetMs are serialized (race R6).
            action_o = '0;
            action_o.send_fwd_getm = 1'b1;
          end""",
        test=RACE_TESTS,
        case="test_r8_no_forward_into_a_dead_mshr",
    ),
    dict(
        name="r9-drop-recalled-dirty-data",
        race="R9",
        file=DIR,
        why="The recall response is the only copy of the owner's stores. "
            "Writing back the L2's own stale line instead -- which is what "
            "happens if the response's data is not captured -- loses every "
            "store made since the line was handed out, with no other symptom.",
        find="""            if (cur_q.msg_type == MSG_WB_DATA) begin
              binv_data_q    <= cur_q.data;
              binv_wb_pend_q <= 1'b1;
            end""",
        replace="""            if (cur_q.msg_type == MSG_WB_DATA) begin
              binv_wb_pend_q <= 1'b1;
            end""",
        test=RACE_TESTS,
        case="test_r9_back_invalidation_hits_m",
    ),
    dict(
        name="r11-drop-unexpected-putm-data",
        race="R11",
        file=DIRFSM,
        why="A directory in E cannot tell whether the owner silently upgraded, "
            "so it must take the data a PutM carries. Dropping it loses the "
            "store made after the silent E->M.",
        find="""          DEV_PUTM_OWNER: begin
            action_o = '0;
            action_o.copy_data_l2 = 1'b1;
            action_o.send_put_ack = 1'b1;
            action_o.clear_owner  = 1'b1;
            next_state_o = DIR_I;
          end
          DEV_PUTE_OWNER: begin""",
        replace="""          DEV_PUTM_OWNER: begin
            action_o = '0;
            action_o.send_put_ack = 1'b1;
            action_o.clear_owner  = 1'b1;
            next_state_o = DIR_I;
          end
          DEV_PUTE_OWNER: begin""",
        test=RACE_TESTS,
        case="test_r11_silent_upgrade_then_eviction",
    ),
    dict(
        name="r12-vc-alloc-ignores-eligibility",
        race="R12",
        file=VCALLOC,
        why="This is bug B14 put back. A VC whose output port has no free VC "
            "in its own vnet still wins stage one, produces no candidate, and "
            "never advances the pointer -- so a blocked VN0 starves the VN1 "
            "and VN2 behind it and the machine deadlocks.",
        find="""      .req_i       (eligible[p]),""",
        replace="""      .req_i       (req_i[p]),""",
        test=RACE_TESTS,
        case="test_r12_blocked_vn0_does_not_stop_vn1_vn2",
    ),
]

# R10 has no entry on purpose. It is the false-sharing measurement: there is no
# arc that handles it, because nothing about it is incorrect. Its value is the
# number it reports, and a mutation table that invented an arc for it would be
# claiming coverage that does not exist.
NO_MUTATION = {"R10": "no handling arc -- a performance measurement, not a "
                      "correctness property"}
