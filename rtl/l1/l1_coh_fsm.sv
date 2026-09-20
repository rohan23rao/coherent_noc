//=============================================================================
// l1_coh_fsm.sv
//
// The thirteen-state L1 coherence table, as a pure combinational transition
// function: (state, event) -> (next state, actions). Nothing else in the
// design encodes a protocol transition, so this file is the single place a
// reviewer has to read to check the protocol, and the single place a mutation
// test has to edit to break one arc.
//
// Interfaces: state_i, event_i in; next_state_o, action_o out. COMBINATIONAL by
// construction -- it is a lookup table, and the controller registers the result.
//
// A blank cell in the specification's table means the event cannot occur in
// that state. Those set action_o.illegal, and the controller raises $error.
// There is deliberately no permissive default: a protocol that silently
// tolerates an impossible event hides the bug that produced it.
//
// Four arcs carry the design and each is commented where it appears:
//   * E on Fwd-GetS sends data to the requester AND the directory
//   * SM_AD on Inv sends an Inv-Ack and falls back to IM_AD
//   * IM_AD on Inv-Ack decrements and stays (the counter must be signed)
//   * MI_A on Fwd-GetM goes to II_A, not I
//=============================================================================

module l1_coh_fsm
  import coh_pkg::*;
(
  input  l1_state_e   state_i,
  input  l1_event_e   event_i,
  output l1_state_e   next_state_o,
  output l1_action_t  action_o
);

  always_comb begin
    // Default: the cell is blank -- the event cannot happen in this state.
    next_state_o = state_i;
    action_o     = '0;
    action_o.illegal = 1'b1;

    unique case (state_i)
      //-----------------------------------------------------------------------
      L1_I: begin
        unique case (event_i)
          EV_LOAD: begin
            action_o = '0; action_o.send_gets = 1'b1;
            next_state_o = L1_IS_D;
          end
          EV_STORE: begin
            action_o = '0; action_o.send_getm = 1'b1;
            next_state_o = L1_IM_AD;
          end
          default: ;   // everything else is blank
        endcase
      end

      //-----------------------------------------------------------------------
      L1_IS_D: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT,
          EV_FWD_GETS, EV_FWD_GETM, EV_INV: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_DATA_E_DIR: begin
            // The directory handed over exclusive ownership on a read.
            action_o = '0; action_o.fill_data = 1'b1; action_o.complete = 1'b1;
            next_state_o = L1_E;
          end
          EV_DATA_DIR_A0: begin
            action_o = '0; action_o.fill_data = 1'b1; action_o.complete = 1'b1;
            next_state_o = L1_S;
          end
          EV_DATA_OWNER: begin
            action_o = '0; action_o.fill_data = 1'b1; action_o.complete = 1'b1;
            next_state_o = L1_S;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_IM_AD: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT,
          EV_FWD_GETS, EV_FWD_GETM, EV_INV: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_DATA_DIR_A0: begin
            action_o = '0; action_o.fill_data = 1'b1; action_o.complete = 1'b1;
            next_state_o = L1_M;
          end
          EV_DATA_DIR_AGT0: begin
            // Data has landed but acks are still outstanding. ack_cnt may
            // already be negative from acks that overtook the data.
            action_o = '0; action_o.fill_data = 1'b1; action_o.ack_add = 1'b1;
            next_state_o = L1_IM_A;
          end
          EV_DATA_OWNER: begin
            action_o = '0; action_o.fill_data = 1'b1; action_o.complete = 1'b1;
            next_state_o = L1_M;
          end
          EV_INV_ACK: begin
            // *** The early-ack arc. An Inv-Ack arrived before the Data that
            // carries the AckCount, so the count goes NEGATIVE and is credited
            // back up when Data lands. This is why ack_cnt is signed.
            action_o = '0; action_o.ack_dec = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_IM_A: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT,
          EV_FWD_GETS, EV_FWD_GETM, EV_INV: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_INV_ACK: begin
            // The controller drives complete when the count reaches zero.
            action_o = '0; action_o.ack_dec = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_S: begin
        unique case (event_i)
          EV_LOAD: begin
            action_o = '0; action_o.hit = 1'b1;
          end
          EV_STORE: begin
            action_o = '0; action_o.send_getm = 1'b1;
            next_state_o = L1_SM_AD;
          end
          EV_EVICT: begin
            action_o = '0; action_o.send_puts = 1'b1;
            next_state_o = L1_SI_A;
          end
          EV_INV: begin
            action_o = '0; action_o.send_inv_ack = 1'b1;
            next_state_o = L1_I;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_SM_AD: begin
        unique case (event_i)
          EV_LOAD: begin
            // Still a legal shared copy until the upgrade is ordered.
            action_o = '0; action_o.hit = 1'b1;
          end
          EV_STORE, EV_EVICT, EV_FWD_GETS, EV_FWD_GETM: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_INV: begin
            // *** Lost the race: somebody else's GetM was ordered first and
            // took this core's shared copy. It MUST ack, or that core waits
            // forever -- and it must now expect full data, not just an
            // AckCount, so it falls back to IM_AD rather than staying here.
            action_o = '0; action_o.send_inv_ack = 1'b1;
            next_state_o = L1_IM_AD;
          end
          EV_DATA_DIR_A0: begin
            action_o = '0; action_o.fill_data = 1'b1; action_o.complete = 1'b1;
            next_state_o = L1_M;
          end
          EV_DATA_DIR_AGT0: begin
            action_o = '0; action_o.fill_data = 1'b1; action_o.ack_add = 1'b1;
            next_state_o = L1_SM_A;
          end
          EV_DATA_OWNER: begin
            action_o = '0; action_o.fill_data = 1'b1; action_o.complete = 1'b1;
            next_state_o = L1_M;
          end
          EV_INV_ACK: begin
            action_o = '0; action_o.ack_dec = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_SM_A: begin
        unique case (event_i)
          EV_LOAD: begin
            action_o = '0; action_o.hit = 1'b1;
          end
          EV_STORE, EV_EVICT, EV_FWD_GETS, EV_FWD_GETM: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_INV_ACK: begin
            action_o = '0; action_o.ack_dec = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_E: begin
        unique case (event_i)
          EV_LOAD: begin
            action_o = '0; action_o.hit = 1'b1;
          end
          EV_STORE: begin
            // Silent upgrade: E already grants write permission.
            action_o = '0; action_o.hit = 1'b1;
            next_state_o = L1_M;
          end
          EV_EVICT: begin
            // E is an ownership state here, so eviction is NOT silent -- the
            // directory cannot otherwise tell whether this core upgraded to M.
            action_o = '0; action_o.send_pute = 1'b1;
            next_state_o = L1_EI_A;
          end
          EV_FWD_GETS: begin
            // *** Data goes to the requester AND to the directory. The
            // directory cannot know whether this core silently went E->M, so
            // its copy must be refreshed unconditionally. This is why S_D
            // exists at the directory.
            action_o = '0;
            action_o.send_data_req = 1'b1;
            action_o.send_data_dir = 1'b1;
            next_state_o = L1_S;
          end
          EV_FWD_GETM: begin
            action_o = '0; action_o.send_data_req = 1'b1;
            next_state_o = L1_I;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_M: begin
        unique case (event_i)
          EV_LOAD, EV_STORE: begin
            action_o = '0; action_o.hit = 1'b1;
          end
          EV_EVICT: begin
            action_o = '0; action_o.send_putm = 1'b1;
            next_state_o = L1_MI_A;
          end
          EV_FWD_GETS: begin
            action_o = '0;
            action_o.send_data_req = 1'b1;
            action_o.send_data_dir = 1'b1;
            next_state_o = L1_S;
          end
          EV_FWD_GETM: begin
            action_o = '0; action_o.send_data_req = 1'b1;
            next_state_o = L1_I;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_MI_A: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_FWD_GETS: begin
            action_o = '0;
            action_o.send_data_req = 1'b1;
            action_o.send_data_dir = 1'b1;
            next_state_o = L1_SI_A;
          end
          EV_FWD_GETM: begin
            // *** Not I. The PutM is still in flight and its Put-Ack is still
            // owed; retiring the MSHR here would leave a message arriving with
            // no state to receive it.
            action_o = '0; action_o.send_data_req = 1'b1;
            next_state_o = L1_II_A;
          end
          EV_PUT_ACK: begin
            action_o = '0; action_o.complete = 1'b1;
            next_state_o = L1_I;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_EI_A: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_FWD_GETS: begin
            action_o = '0;
            action_o.send_data_req = 1'b1;
            action_o.send_data_dir = 1'b1;
            next_state_o = L1_SI_A;
          end
          EV_FWD_GETM: begin
            action_o = '0; action_o.send_data_req = 1'b1;
            next_state_o = L1_II_A;
          end
          EV_PUT_ACK: begin
            action_o = '0; action_o.complete = 1'b1;
            next_state_o = L1_I;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_SI_A: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_INV: begin
            action_o = '0; action_o.send_inv_ack = 1'b1;
            next_state_o = L1_II_A;
          end
          EV_PUT_ACK: begin
            action_o = '0; action_o.complete = 1'b1;
            next_state_o = L1_I;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      L1_II_A: begin
        unique case (event_i)
          EV_LOAD, EV_STORE, EV_EVICT: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          EV_PUT_ACK: begin
            action_o = '0; action_o.complete = 1'b1;
            next_state_o = L1_I;
          end
          // Race R8: a forward arriving here is IMPOSSIBLE, not merely
          // unhandled. The directory removes a requester from the sharer list
          // before sending its Put-Ack, so it cannot generate a forward for a
          // core that is already in II_A. If the illegal flag ever fires on
          // EV_FWD_GETS or EV_FWD_GETM here, that is a directory ordering bug
          // and must be fixed there, not absorbed by adding an arc here.
          default: ;
        endcase
      end

      default: begin
        action_o = '0;
        action_o.illegal = 1'b1;
      end
    endcase
  end

endmodule : l1_coh_fsm
