//=============================================================================
// msg_mux.sv  (TESTBENCH SCAFFOLDING -- not part of rtl/)
//
// Round-robin merge of N coherence-message sources onto one destination port,
// used by the Phase 6/7 direct-connect harness in place of the network. Only
// sources whose `sel` bit is set are eligible, which is how a destination picks
// out the messages addressed to it.
//
// It exists so that a protocol bug in Phases 6-7 can never be a network bug:
// this is the simplest thing that delivers messages in *some* order with
// backpressure, and nothing more.
//=============================================================================

module msg_mux
  import coh_pkg::*;
#(
  parameter int unsigned N = 4
) (
  input  logic               clk,
  input  logic               rst_n,
  input  logic [N-1:0]       src_valid_i,
  input  logic [N-1:0]       src_sel_i,
  input  coh_msg_t [N-1:0]   src_msg_i,
  output logic [N-1:0]       src_grant_o,
  output logic               dst_valid_o,
  input  logic               dst_ready_i,
  output coh_msg_t           dst_msg_o
);

  logic [N-1:0]        req;
  logic [N-1:0]        gnt;
  logic                gnt_valid;
  logic [$clog2(N)-1:0] gnt_idx;

  assign req = src_valid_i & src_sel_i;

  rr_arbiter #(.N(N)) u_arb (
    .clk (clk), .rst_n (rst_n),
    .req_i (req), .take_i (dst_ready_i),
    .gnt_o (gnt), .gnt_valid_o (gnt_valid), .gnt_idx_o (gnt_idx)
  );

  assign dst_valid_o = gnt_valid;
  assign dst_msg_o   = src_msg_i[gnt_idx];
  assign src_grant_o = (gnt_valid && dst_ready_i) ? gnt : '0;

endmodule : msg_mux
