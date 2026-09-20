//=============================================================================
// system_top.sv
//
// System top level. At Phase 0 it holds only the two things the hard
// constraints require of a top: the single reset synchronizer whose output is
// fanned out to every tile, and the free-running cycle counter the network and
// stress phases measure against. Tiles, memory and the mesh arrive in later
// phases.
//
// Interfaces: clk, arst_n in; rst_n_o and cycle_count_o out, both registered.
//
// The one non-obvious thing: reset is synchronized exactly once, here, and
// distributed. No module under rtl/ may synchronize its own reset, or the
// design would have as many reset-removal timing paths as it has synchronizers.
//=============================================================================

module system_top
  import coh_pkg::*;
#(
  parameter int unsigned CYCLE_CNT_W = 48
) (
  input  logic                    clk,
  input  logic                    arst_n,
  output logic                    rst_n_o,
  output logic [CYCLE_CNT_W-1:0]  cycle_count_o
);

  logic rst_n;

  reset_sync #(
    .STAGES (2)
  ) u_reset_sync (
    .clk    (clk),
    .arst_n (arst_n),
    .rst_n  (rst_n)
  );

  logic [CYCLE_CNT_W-1:0] cycle_count_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cycle_count_q <= '0;
    end else begin
      cycle_count_q <= cycle_count_q + CYCLE_CNT_W'(1);
    end
  end

  assign rst_n_o       = rst_n;
  assign cycle_count_o = cycle_count_q;

endmodule : system_top
