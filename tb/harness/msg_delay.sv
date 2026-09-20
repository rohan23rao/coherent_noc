//=============================================================================
// msg_delay.sv  (TESTBENCH SCAFFOLDING -- not part of rtl/)
//
// Holds one coherence message for a programmable number of cycles before
// presenting it downstream. This is the deterministic-interleaving hook: by
// slowing one source's messages relative to another's, a test can force the
// exact order that a named race requires, without touching rtl/ and without
// adding settle cycles anywhere.
//
// delay_i == 0 is a pure pass-through with a one-cycle register, so the hook
// can be left in place for every test and costs the same latency whether or
// not it is being used.
//
// Depth one, deliberately: a message is held and the source is backpressured
// behind it, which preserves per-source ordering. A deeper delay element would
// let a later message overtake an earlier one from the same source, which is
// not a race the protocol is required to survive and would make failures
// unreproducible.
//=============================================================================

module msg_delay
  import coh_pkg::*;
(
  input  logic        clk,
  input  logic        rst_n,
  input  logic [7:0]  delay_i,

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
          count_q <= delay_i;
        end
      end else if (count_q != 8'd0) begin
        count_q <= count_q - 8'd1;
      end else if (out_ready_i) begin
        busy_q <= 1'b0;
      end
    end
  end

endmodule : msg_delay
