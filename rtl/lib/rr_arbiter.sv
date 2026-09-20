//=============================================================================
// rr_arbiter.sv
//
// Parameterized round-robin arbiter, mask-and-rotate style. One instance is
// used everywhere arbitration is needed -- VC allocation, switch allocation,
// the L1 request FSM -- so that fairness is a property of one reviewed module
// rather than of five hand-rolled ones.
//
// Interfaces: clk, rst_n; req_i (one bit per requester), take_i (the grant was
// consumed this cycle); gnt_o / gnt_valid_o / gnt_idx_o.
//
// gnt_o, gnt_valid_o and gnt_idx_o are COMBINATIONAL in req_i. This is a
// deliberate exception to the registered-output rule and is required: an
// allocator stage must see the grant in the same cycle it presents the request,
// and the stage boundary registers the result. Only the priority pointer is
// sequential.
//
// The one non-obvious thing: the pointer advances only when take_i is asserted.
// A cycle with requests but no grant taken must NOT rotate, or a requester that
// is repeatedly offered a grant it cannot consume is walked past and starves --
// which is exactly the failure mode that makes a "fair" arbiter unfair under
// backpressure.
//=============================================================================

module rr_arbiter #(
  parameter  int unsigned N     = 4,
  // $clog2(1) is 0; keep the index port at least one bit wide.
  localparam int unsigned IDX_W = (N > 1) ? $clog2(N) : 1
) (
  input  logic                clk,
  input  logic                rst_n,
  input  logic [N-1:0]        req_i,
  input  logic                take_i,
  output logic [N-1:0]        gnt_o,
  output logic                gnt_valid_o,
  output logic [IDX_W-1:0]    gnt_idx_o
);

  logic [IDX_W-1:0] ptr_q;

  // Requests at or above the pointer.
  logic [N-1:0] mask;
  logic [N-1:0] masked_req;
  logic [N-1:0] masked_gnt;
  logic [N-1:0] unmasked_gnt;

  always_comb begin
    mask = '0;
    for (int unsigned i = 0; i < N; i++) begin
      mask[i] = (IDX_W'(i) >= ptr_q);
    end
  end

  assign masked_req = req_i & mask;

  // Isolate the lowest set bit: x & (-x).
  assign masked_gnt   = masked_req & (~masked_req + {{(N-1){1'b0}}, 1'b1});
  assign unmasked_gnt = req_i      & (~req_i      + {{(N-1){1'b0}}, 1'b1});

  // Prefer a requester at or above the pointer; otherwise wrap to the lowest.
  assign gnt_o       = (|masked_req) ? masked_gnt : unmasked_gnt;
  assign gnt_valid_o = |req_i;

  always_comb begin
    gnt_idx_o = '0;
    for (int unsigned i = 0; i < N; i++) begin
      if (gnt_o[i]) begin
        gnt_idx_o = IDX_W'(i);
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ptr_q <= '0;
    end else if (gnt_valid_o && take_i) begin
      // Next requester after the winner, wrapping.
      ptr_q <= (gnt_idx_o == IDX_W'(N - 1)) ? '0 : (gnt_idx_o + IDX_W'(1));
    end
  end

`ifndef SYNTHESIS
  // A grant is one-hot exactly when there is a request, and zero otherwise.
  a_gnt_onehot : assert property (@(posedge clk) disable iff (!rst_n)
    $onehot0(gnt_o))
    else $error("rr_arbiter: grant is not one-hot-zero");

  a_gnt_implies_req : assert property (@(posedge clk) disable iff (!rst_n)
    (|gnt_o) |-> ((gnt_o & req_i) == gnt_o))
    else $error("rr_arbiter: granted a requester that was not requesting");

  a_req_implies_gnt : assert property (@(posedge clk) disable iff (!rst_n)
    (|req_i) |-> (|gnt_o))
    else $error("rr_arbiter: request present but no grant issued");
`endif

endmodule : rr_arbiter
