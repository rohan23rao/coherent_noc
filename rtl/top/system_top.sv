//=============================================================================
// system_top.sv
//
// The whole machine: four tiles on a 2x2 mesh, each with an L1, a directory
// bank, its memory and a network interface, plus the single reset
// synchronizer and the free-running cycle counter.
//
// Interfaces: clk, arst_n; per-tile core request and response channels; the
// per-virtual-network message holds; and the debug buses the coherence
// checker reads.
//
// The one non-obvious thing: reset is synchronized exactly ONCE, here, and
// fanned out. No module below synchronizes its own, or the design would have
// as many reset-removal timing paths as it has synchronizers, and two blocks
// could leave reset on different cycles. See docs/decisions.md D6.
//=============================================================================

module system_top
  import coh_pkg::*;
#(
  parameter int unsigned MEM_LINES   = 1024,
  parameter int unsigned MEM_LAT     = 8,
  parameter bit          ENABLE_E    = 1'b1,
  parameter int unsigned TBE_TO      = TBE_TIMEOUT,
  parameter int unsigned CYCLE_CNT_W = 48
) (
  input  logic                                     clk,
  input  logic                                     arst_n,
  output logic                                     rst_n_o,
  output logic [CYCLE_CNT_W-1:0]                   cycle_count_o,

  input  logic [NUM_TILES-1:0]                     core_req_valid_i,
  output logic [NUM_TILES-1:0]                     core_req_ready_o,
  input  logic [NUM_TILES-1:0][1:0]                core_op_i,
  input  logic [NUM_TILES-1:0][ADDR_W-1:0]         core_addr_i,
  input  logic [NUM_TILES-1:0][WORD_W-1:0]         core_wdata_i,
  input  logic [NUM_TILES-1:0][BE_W-1:0]           core_be_i,
  input  logic [NUM_TILES-1:0][CORE_TAG_W-1:0]     core_tag_i,

  output logic [NUM_TILES-1:0]                     core_resp_valid_o,
  output logic [NUM_TILES-1:0][CORE_TAG_W-1:0]     core_resp_tag_o,
  output logic [NUM_TILES-1:0][WORD_W-1:0]         core_resp_rdata_o,

  input  logic [NUM_TILES-1:0][7:0]                hold_vn0_i,
  input  logic [NUM_TILES-1:0][7:0]                hold_vn1_i,
  input  logic [NUM_TILES-1:0][7:0]                hold_vn2_i,
  input  logic [NUM_TILES-1:0][7:0]                hold_vn2_dir_i,

  input  logic [L1_IDX_W-1:0]                      dbg_set_i,
  output logic [NUM_TILES-1:0][L1_WAYS-1:0][3:0]   dbg_state_o,
  output logic [NUM_TILES-1:0][L1_WAYS-1:0][L1_TAG_W-1:0] dbg_tag_o,
  output logic [NUM_TILES-1:0][MSHR_ENTRIES-1:0]   dbg_mshr_valid_o,
  input  logic [L2_IDX_W-1:0]                      dbg_dir_set_i,
  input  logic [L2_WAY_W-1:0]                      dbg_dir_way_i,
  output dir_meta_t [NUM_TILES-1:0]                dbg_dir_meta_o
);

  logic rst_n;

  reset_sync #(.STAGES (2)) u_reset_sync (
    .clk (clk), .arst_n (arst_n), .rst_n (rst_n)
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

  logic  [NUM_TILES-1:0]                 inj_valid, eje_valid;
  flit_t [NUM_TILES-1:0]                 inj_flit,  eje_flit;
  logic  [NUM_TILES-1:0]                 inj_cr_valid, eje_cr_valid;
  logic  [NUM_TILES-1:0][VC_SEL_W-1:0]   inj_cr_vc,    eje_cr_vc;
  logic  [NUM_TILES-1:0]                 inj_cr_tail,  eje_cr_tail;

  for (genvar t = 0; t < int'(NUM_TILES); t++) begin : gen_tile
    tile_top #(
      .TILE_ID   (t),
      .MEM_LINES (MEM_LINES),
      .MEM_LAT   (MEM_LAT),
      .ENABLE_E  (ENABLE_E),
      .TBE_TO    (TBE_TO)
    ) u_tile (
      .clk (clk), .rst_n (rst_n),
      .core_req_valid_i (core_req_valid_i[t]),
      .core_req_ready_o (core_req_ready_o[t]),
      .core_op_i        (core_op_e'(core_op_i[t])),
      .core_addr_i      (core_addr_i[t]),
      .core_wdata_i     (core_wdata_i[t]),
      .core_be_i        (core_be_i[t]),
      .core_tag_i       (core_tag_i[t]),
      .core_resp_valid_o(core_resp_valid_o[t]),
      .core_resp_tag_o  (core_resp_tag_o[t]),
      .core_resp_rdata_o(core_resp_rdata_o[t]),
      .flit_valid_o   (inj_valid[t]),
      .flit_o         (inj_flit[t]),
      .credit_valid_i (inj_cr_valid[t]),
      .credit_vc_i    (inj_cr_vc[t]),
      .credit_tail_i  (inj_cr_tail[t]),
      .flit_valid_i   (eje_valid[t]),
      .flit_i         (eje_flit[t]),
      .credit_valid_o (eje_cr_valid[t]),
      .credit_vc_o    (eje_cr_vc[t]),
      .credit_tail_o  (eje_cr_tail[t]),
      .hold_vn0_i (hold_vn0_i[t]),
      .hold_vn1_i (hold_vn1_i[t]),
      .hold_vn2_i (hold_vn2_i[t]),
      .hold_vn2_dir_i (hold_vn2_dir_i[t]),
      .dbg_set_i (dbg_set_i),
      .dbg_state_o (dbg_state_o[t]),
      .dbg_tag_o (dbg_tag_o[t]),
      .dbg_mshr_valid_o (dbg_mshr_valid_o[t]),
      .dbg_dir_set_i (dbg_dir_set_i),
      .dbg_dir_way_i (dbg_dir_way_i),
      .dbg_dir_meta_o (dbg_dir_meta_o[t])
    );
  end

  noc_top u_noc (
    .clk (clk), .rst_n (rst_n),
    .inject_valid_i         (inj_valid),
    .inject_flit_i          (inj_flit),
    .inject_credit_valid_o  (inj_cr_valid),
    .inject_credit_vc_o     (inj_cr_vc),
    .inject_credit_tail_o   (inj_cr_tail),
    .eject_valid_o          (eje_valid),
    .eject_flit_o           (eje_flit),
    .eject_credit_valid_i   (eje_cr_valid),
    .eject_credit_vc_i      (eje_cr_vc),
    .eject_credit_tail_i    (eje_cr_tail)
  );

endmodule : system_top
