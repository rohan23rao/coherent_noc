//=============================================================================
// reset_sync.sv
//
// Asynchronous-assert / synchronous-de-assert reset synchronizer.
//
// Interfaces: clk, arst_n (raw async reset in), rst_n (synchronized out).
//
// The one non-obvious thing: the flops are reset by arst_n itself, so assertion
// is immediate and needs no running clock, while de-assertion walks a shift
// register of 1s and therefore lands synchronously. Recovery/removal timing is
// met at exactly one flop -- the last stage -- instead of at every flop in the
// design, which is the whole reason reset is synchronized once in the top and
// fanned out rather than being distributed raw.
//=============================================================================

module reset_sync #(
  parameter int unsigned STAGES = 2
) (
  input  logic clk,
  input  logic arst_n,
  output logic rst_n
);

  if (STAGES < 2) begin : gen_param_check
    $error("reset_sync: STAGES must be >= 2");
  end

  logic [STAGES-1:0] sync_q;

  always_ff @(posedge clk or negedge arst_n) begin
    if (!arst_n) begin
      sync_q <= '0;
    end else begin
      sync_q <= {sync_q[STAGES-2:0], 1'b1};
    end
  end

  assign rst_n = sync_q[STAGES-1];

endmodule : reset_sync
