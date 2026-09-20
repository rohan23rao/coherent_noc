//=============================================================================
// dir_coh_fsm.sv
//
// The five-state directory table, as a pure combinational transition function:
// (state, event) -> (next state, actions). The counterpart of l1_coh_fsm, and
// for the same reason: one reviewable place for the protocol, and one line for
// a mutation test to break.
//
// Interfaces: state_i, event_i, enable_e_i in; next_state_o, action_o out.
// COMBINATIONAL.
//
// enable_e_i selects MSI or MESI, and it changes exactly one arc: I + GetS.
// With E disabled the directory hands out a shared copy and goes to S; with it
// enabled it hands out DataE, records an owner and goes to E. Keeping the
// difference to one arc, visible in one place, is what makes the Phase 6 to
// Phase 7 step reviewable -- and it is the honest answer to "what does adding
// E actually cost you".
//
// Four notes the specification requires in the RTL, at the arcs they describe.
//=============================================================================

module dir_coh_fsm
  import coh_pkg::*;
(
  input  logic         enable_e_i,
  input  dir_state_e   state_i,
  input  dir_event_e   event_i,
  output dir_state_e   next_state_o,
  output dir_action_t  action_o
);

  always_comb begin
    next_state_o     = state_i;
    action_o         = '0;
    action_o.illegal = 1'b1;

    unique case (state_i)
      //-----------------------------------------------------------------------
      DIR_I: begin
        unique case (event_i)
          DEV_GETS: begin
            if (enable_e_i) begin
              // *** DataE and E, not Data and S. This is the entire point of
              // adding E: a read-then-write costs one transaction instead of
              // two. It is also why a PutE transaction has to exist -- the
              // directory has handed out ownership and must be told when it
              // comes back.
              action_o = '0;
              action_o.send_data_e   = 1'b1;
              action_o.set_owner_req = 1'b1;
              next_state_o = DIR_E;
            end else begin
              action_o = '0;
              action_o.send_data  = 1'b1;
              action_o.add_sharer = 1'b1;
              next_state_o = DIR_S;
            end
          end
          DEV_GETM: begin
            action_o = '0;
            action_o.send_data     = 1'b1;
            action_o.set_owner_req = 1'b1;
            next_state_o = DIR_M;
          end
          // A Put for a line the directory thinks nobody holds is stale but
          // harmless: ack it and change nothing.
          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST,
          DEV_PUTM_NON_OWNER, DEV_PUTE_NON_OWNER: begin
            action_o = '0; action_o.send_put_ack = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      DIR_S: begin
        unique case (event_i)
          DEV_GETS: begin
            action_o = '0;
            action_o.send_data  = 1'b1;
            action_o.add_sharer = 1'b1;
          end
          DEV_GETM: begin
            // *** Data plus AckCount even when the requester is already a
            // sharer. The primer's simplification: the requester in SM_AD
            // already holds valid data, so a bare AckCount would do. The
            // optimized version saves one line of network data per upgrade and
            // costs a new message type plus a distinct SM_A entry arc.
            action_o = '0;
            action_o.send_data       = 1'b1;
            action_o.send_inv_others = 1'b1;
            action_o.clear_sharers   = 1'b1;
            action_o.set_owner_req   = 1'b1;
            next_state_o = DIR_M;
          end
          DEV_PUTS_NOT_LAST: begin
            action_o = '0;
            action_o.remove_sharer = 1'b1;
            action_o.send_put_ack  = 1'b1;
          end
          DEV_PUTS_LAST: begin
            action_o = '0;
            action_o.remove_sharer = 1'b1;
            action_o.send_put_ack  = 1'b1;
            next_state_o = DIR_I;
          end
          DEV_PUTM_NON_OWNER, DEV_PUTE_NON_OWNER: begin
            action_o = '0;
            action_o.remove_sharer = 1'b1;
            action_o.send_put_ack  = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      DIR_E: begin
        unique case (event_i)
          DEV_GETS: begin
            action_o = '0;
            action_o.send_fwd_gets    = 1'b1;
            action_o.sharers_owner_req = 1'b1;
            action_o.clear_owner      = 1'b1;
            next_state_o = DIR_S_D;
          end
          DEV_GETM: begin
            action_o = '0;
            action_o.send_fwd_getm = 1'b1;
            action_o.set_owner_req = 1'b1;
            next_state_o = DIR_M;
          end
          // *** A PutS while in E is stale: a sharer evicted, then the line
          // went to E at another core before the PutS landed. Ack it, change
          // nothing.
          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST: begin
            action_o = '0; action_o.send_put_ack = 1'b1;
          end
          DEV_PUTM_OWNER: begin
            action_o = '0;
            action_o.copy_data_l2 = 1'b1;
            action_o.send_put_ack = 1'b1;
            action_o.clear_owner  = 1'b1;
            next_state_o = DIR_I;
          end
          DEV_PUTE_OWNER: begin
            action_o = '0;
            action_o.send_put_ack = 1'b1;
            action_o.clear_owner  = 1'b1;
            next_state_o = DIR_I;
          end
          // *** PutE from a NON-owner while in E: core A evicted E, the
          // directory reassigned E to core B, and A's PutE arrived after. Ack
          // it and do NOT clear the owner. Getting this check wrong loses B's
          // ownership and, with it, B's data.
          DEV_PUTM_NON_OWNER, DEV_PUTE_NON_OWNER: begin
            action_o = '0; action_o.send_put_ack = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      DIR_M: begin
        unique case (event_i)
          DEV_GETS: begin
            action_o = '0;
            action_o.send_fwd_gets     = 1'b1;
            action_o.sharers_owner_req = 1'b1;
            action_o.clear_owner       = 1'b1;
            next_state_o = DIR_S_D;
          end
          DEV_GETM: begin
            // Stays in M; the owner simply changes. This is how two
            // back-to-back GetMs are serialized (race R6).
            action_o = '0;
            action_o.send_fwd_getm = 1'b1;
            action_o.set_owner_req = 1'b1;
          end
          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST: begin
            action_o = '0; action_o.send_put_ack = 1'b1;
          end
          DEV_PUTM_OWNER: begin
            // Race R11: the owner had E, silently upgraded to M, and is now
            // evicting with data while the directory still recorded E. The
            // data must be taken.
            action_o = '0;
            action_o.copy_data_l2 = 1'b1;
            action_o.send_put_ack = 1'b1;
            action_o.clear_owner  = 1'b1;
            next_state_o = DIR_I;
          end
          DEV_PUTM_NON_OWNER, DEV_PUTE_OWNER, DEV_PUTE_NON_OWNER: begin
            action_o = '0; action_o.send_put_ack = 1'b1;
          end
          default: ;
        endcase
      end

      //-----------------------------------------------------------------------
      DIR_S_D: begin
        unique case (event_i)
          // *** S_D stalls GetS and GetM but accepts every Put. Stalling the
          // Puts too would deadlock: the cache that owes this directory its
          // data may itself be waiting on a Put-Ack from here.
          DEV_GETS, DEV_GETM: begin
            action_o = '0; action_o.stall = 1'b1;
          end
          DEV_PUTS_NOT_LAST, DEV_PUTS_LAST,
          DEV_PUTM_OWNER, DEV_PUTM_NON_OWNER,
          DEV_PUTE_OWNER, DEV_PUTE_NON_OWNER: begin
            action_o = '0;
            action_o.remove_sharer = 1'b1;
            action_o.send_put_ack  = 1'b1;
          end
          DEV_DATA: begin
            action_o = '0;
            action_o.copy_data_l2 = 1'b1;
            next_state_o = DIR_S;
          end
          default: ;
        endcase
      end

      default: begin
        action_o         = '0;
        action_o.illegal = 1'b1;
      end
    endcase
  end

endmodule : dir_coh_fsm
