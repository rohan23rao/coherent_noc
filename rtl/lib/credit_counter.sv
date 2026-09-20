//=============================================================================
// credit_counter.sv
//
// One downstream VC's worth of credit. Initialized to DEPTH, decremented when a
// flit is launched, incremented when the downstream buffer frees a slot.
//
// Interfaces: clk, rst_n; send_i (flit launched this cycle), credit_ret_i
// (credit returned this cycle); credits_o and has_credit_o, both registered.
//
// The one non-obvious thing: send_i and credit_ret_i may both be asserted in the
// same cycle and the counter must then hold. Treating them as independent
// increments and letting them race is the classic off-by-one that shows up only
// under sustained full-rate traffic, which is the case least likely to be in a
// directed test.
//=============================================================================

module credit_counter #(
  parameter  int unsigned DEPTH = 4,
  localparam int unsigned CNT_W = $clog2(DEPTH + 1)
) (
  input  logic                clk,
  input  logic                rst_n,
  input  logic                send_i,
  input  logic                credit_ret_i,
  output logic [CNT_W-1:0]    credits_o,
  output logic                has_credit_o
);

  logic [CNT_W-1:0] credits_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      credits_q <= CNT_W'(DEPTH);
    end else begin
      unique case ({send_i, credit_ret_i})
        2'b10:   credits_q <= credits_q - CNT_W'(1);
        2'b01:   credits_q <= credits_q + CNT_W'(1);
        default: credits_q <= credits_q;   // 2'b00 and 2'b11 both hold
      endcase
    end
  end

  assign credits_o    = credits_q;
  assign has_credit_o = (credits_q != CNT_W'(0));

`ifndef SYNTHESIS
  a_no_send_without_credit : assert property (@(posedge clk) disable iff (!rst_n)
    send_i |-> (credits_q != CNT_W'(0)))
    else $error("credit_counter: flit launched with zero credits");

  a_no_underflow : assert property (@(posedge clk) disable iff (!rst_n)
    !(send_i && !credit_ret_i && (credits_q == CNT_W'(0))))
    else $error("credit_counter: underflow");

  a_no_overflow : assert property (@(posedge clk) disable iff (!rst_n)
    credits_q <= CNT_W'(DEPTH))
    else $error("credit_counter: count exceeded DEPTH");
`endif

endmodule : credit_counter
