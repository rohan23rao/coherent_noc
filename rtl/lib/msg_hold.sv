//=============================================================================
// msg_hold.sv
//
// A registered ready/valid stage on the message path whose latency is an
// input: hold_i cycles beyond the register itself.
//
// Interfaces: clk, rst_n, hold_i; an upstream ready/valid message port and a
// downstream one.
//
// At hold_i == 0 this is an ordinary one-deep register slice, which is what
// the NIC boundary wants anyway. Above zero it delays this source's messages
// relative to every other source, which is how a directed race test forces an
// exact interleaving -- see docs/decisions.md D16.
//
// The one non-obvious thing: depth ONE. A deeper element would let a later
// message from the same source overtake an earlier one, which is not an
// ordering the protocol has to survive and would make a forced race depend on
// queue occupancy -- that is, unreproducible. Backpressuring the source
// instead preserves per-source order exactly.
//=============================================================================

module msg_hold
  import coh_pkg::*;
(
  input  logic        clk,
  input  logic        rst_n,
  input  logic [7:0]  hold_i,

  input  logic        in_valid_i,
  output logic        in_ready_o,
  input  coh_msg_t    in_msg_i,

  output logic        out_valid_o,
  input  logic        out_ready_i,
  output coh_msg_t    out_msg_o
);

  coh_msg_t   held_q;
  logic       busy_q;
  logic [7:0] count_q;

  assign in_ready_o  = !busy_q;
  assign out_valid_o = busy_q && (count_q == 8'd0);
  assign out_msg_o   = held_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      held_q  <= '0;
      busy_q  <= 1'b0;
      count_q <= 8'd0;
    end else begin
      if (!busy_q) begin
        if (in_valid_i) begin
          held_q  <= in_msg_i;
          busy_q  <= 1'b1;
          count_q <= hold_i;
        end
      end else if (count_q != 8'd0) begin
        count_q <= count_q - 8'd1;
      end else if (out_ready_i) begin
        busy_q <= 1'b0;
      end
    end
  end

endmodule : msg_hold
