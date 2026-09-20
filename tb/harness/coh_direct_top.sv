//=============================================================================
// coh_direct_top.sv  (TESTBENCH SCAFFOLDING -- not part of rtl/)
//
// Four tiles, each with an L1 and a directory bank, wired directly to one
// another with no network. Phase 8 replaces the wiring with tile_nic and
// noc_top; until then this exists so that a protocol bug is never confused
// with a network bug.
//
// Message routing, by virtual network:
//   VN0  L1 -> directory, selected by the message's dst (the home bank)
//   VN1  directory -> L1
//   VN2  directory -> L1, and L1 -> {L1, directory}. On VN2 the destination is
//        a directory exactly when the message is WB-Data; everything else on
//        VN2 is addressed to a cache.
//
// Each bank gets its own memory model. That is not a simplification of the
// address space: a bank is the home for the quarter of it that addr[6:5]
// selects, so the four memories hold disjoint sets of lines and are
// indistinguishable from one shared memory.
//=============================================================================

module coh_direct_top
  import coh_pkg::*;
#(
  parameter int unsigned MEM_LINES     = 1024,
  parameter int unsigned MEM_LATENCY_P = 8,
  parameter bit          ENABLE_E      = 1'b0
) (
  input  logic                                     clk,
  input  logic                                     rst_n,

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

  // Debug bus for the coherence checker: per tile, the state and tag of a
  // selected (set, way).
  input  logic [L1_IDX_W-1:0]                      dbg_set_i,
  output logic [NUM_TILES-1:0][L1_WAYS-1:0][3:0]   dbg_state_o,
  output logic [NUM_TILES-1:0][L1_WAYS-1:0][L1_TAG_W-1:0] dbg_tag_o,
  output logic [NUM_TILES-1:0][MSHR_ENTRIES-1:0]   dbg_mshr_valid_o,

  input  logic [L2_IDX_W-1:0]                      dbg_dir_set_i,
  input  logic [L2_WAY_W-1:0]                      dbg_dir_way_i,
  output dir_meta_t [NUM_TILES-1:0]                dbg_dir_meta_o,

  // Per-source, per-virtual-network delay in cycles. Zero is pass-through.
  // This is what lets a directed race test force an exact interleaving.
  input  logic [NUM_TILES-1:0][7:0]                delay_vn0_i,
  input  logic [NUM_TILES-1:0][7:0]                delay_vn1_i,
  input  logic [NUM_TILES-1:0][7:0]                delay_vn2_i
);

  // L1 ports. The *_raw nets come straight out of the modules; the undecorated
  // ones are what the muxes see, after the delay hook.
  logic     [NUM_TILES-1:0] l1_vn0_raw_valid, l1_vn0_raw_ready;
  coh_msg_t [NUM_TILES-1:0] l1_vn0_raw_msg;
  logic     [NUM_TILES-1:0] l1_vn0_valid, l1_vn0_ready;
  coh_msg_t [NUM_TILES-1:0] l1_vn0_msg;
  logic     [NUM_TILES-1:0] l1_vn1_valid, l1_vn1_ready;
  coh_msg_t [NUM_TILES-1:0] l1_vn1_msg;
  logic     [NUM_TILES-1:0] l1_vn2i_valid, l1_vn2i_ready;
  coh_msg_t [NUM_TILES-1:0] l1_vn2i_msg;
  logic     [NUM_TILES-1:0] l1_vn2o_raw_valid, l1_vn2o_raw_ready;
  coh_msg_t [NUM_TILES-1:0] l1_vn2o_raw_msg;
  logic     [NUM_TILES-1:0] l1_vn2o_valid, l1_vn2o_ready;
  coh_msg_t [NUM_TILES-1:0] l1_vn2o_msg;

  // Directory ports
  logic     [NUM_TILES-1:0] d_vn0_valid, d_vn0_ready;
  coh_msg_t [NUM_TILES-1:0] d_vn0_msg;
  logic     [NUM_TILES-1:0] d_vn2i_valid, d_vn2i_ready;
  coh_msg_t [NUM_TILES-1:0] d_vn2i_msg;
  logic     [NUM_TILES-1:0] d_vn1_raw_valid, d_vn1_raw_ready;
  coh_msg_t [NUM_TILES-1:0] d_vn1_raw_msg;
  logic     [NUM_TILES-1:0] d_vn1_valid, d_vn1_ready;
  coh_msg_t [NUM_TILES-1:0] d_vn1_msg;
  logic     [NUM_TILES-1:0] d_vn2o_valid, d_vn2o_ready;
  coh_msg_t [NUM_TILES-1:0] d_vn2o_msg;

  for (genvar t = 0; t < int'(NUM_TILES); t++) begin : gen_tile
    l1_cache u_l1 (
      .clk (clk), .rst_n (rst_n), .tile_id_i (TILE_ID_W'(t)),
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
      .vn0_valid_o (l1_vn0_raw_valid[t]), .vn0_ready_i (l1_vn0_raw_ready[t]),
      .vn0_msg_o   (l1_vn0_raw_msg[t]),
      .vn1_valid_i (l1_vn1_valid[t]), .vn1_ready_o (l1_vn1_ready[t]),
      .vn1_msg_i   (l1_vn1_msg[t]),
      .vn2_valid_i (l1_vn2i_valid[t]), .vn2_ready_o (l1_vn2i_ready[t]),
      .vn2_msg_i   (l1_vn2i_msg[t]),
      .vn2_valid_o (l1_vn2o_raw_valid[t]), .vn2_ready_i (l1_vn2o_raw_ready[t]),
      .vn2_msg_o   (l1_vn2o_raw_msg[t]),
      .dbg_set_i (dbg_set_i),
      .dbg_state_o (dbg_state_o[t]), .dbg_tag_o (dbg_tag_o[t]),
      .dbg_mshr_valid_o (dbg_mshr_valid_o[t])
    );

    logic                   mem_req_valid, mem_req_ready, mem_req_we;
    logic [LINE_ADDR_W-1:0] mem_req_addr;
    logic [LINE_W-1:0]      mem_req_wdata, mem_resp_rdata;
    logic                   mem_resp_valid;

    dir_ctrl #(.BANK_ID (t), .ENABLE_E (ENABLE_E)) u_dir (
      .clk (clk), .rst_n (rst_n),
      .vn0_valid_i (d_vn0_valid[t]), .vn0_ready_o (d_vn0_ready[t]),
      .vn0_msg_i   (d_vn0_msg[t]),
      .vn2_valid_i (d_vn2i_valid[t]), .vn2_ready_o (d_vn2i_ready[t]),
      .vn2_msg_i   (d_vn2i_msg[t]),
      .vn1_valid_o (d_vn1_raw_valid[t]), .vn1_ready_i (d_vn1_raw_ready[t]),
      .vn1_msg_o   (d_vn1_raw_msg[t]),
      .vn2_valid_o (d_vn2o_valid[t]), .vn2_ready_i (d_vn2o_ready[t]),
      .vn2_msg_o   (d_vn2o_msg[t]),
      .mem_req_valid_o (mem_req_valid), .mem_req_ready_i (mem_req_ready),
      .mem_req_addr_o (mem_req_addr), .mem_req_we_o (mem_req_we),
      .mem_req_wdata_o (mem_req_wdata),
      .mem_resp_valid_i (mem_resp_valid), .mem_resp_rdata_i (mem_resp_rdata),
      .dbg_set_i (dbg_dir_set_i), .dbg_way_i (dbg_dir_way_i),
      .dbg_meta_o (dbg_dir_meta_o[t])
    );

    msg_delay u_delay_vn0 (
      .clk (clk), .rst_n (rst_n), .delay_i (delay_vn0_i[t]),
      .in_valid_i (l1_vn0_raw_valid[t]), .in_ready_o (l1_vn0_raw_ready[t]),
      .in_msg_i (l1_vn0_raw_msg[t]),
      .out_valid_o (l1_vn0_valid[t]), .out_ready_i (l1_vn0_ready[t]),
      .out_msg_o (l1_vn0_msg[t])
    );

    msg_delay u_delay_vn1 (
      .clk (clk), .rst_n (rst_n), .delay_i (delay_vn1_i[t]),
      .in_valid_i (d_vn1_raw_valid[t]), .in_ready_o (d_vn1_raw_ready[t]),
      .in_msg_i (d_vn1_raw_msg[t]),
      .out_valid_o (d_vn1_valid[t]), .out_ready_i (d_vn1_ready[t]),
      .out_msg_o (d_vn1_msg[t])
    );

    msg_delay u_delay_vn2 (
      .clk (clk), .rst_n (rst_n), .delay_i (delay_vn2_i[t]),
      .in_valid_i (l1_vn2o_raw_valid[t]), .in_ready_o (l1_vn2o_raw_ready[t]),
      .in_msg_i (l1_vn2o_raw_msg[t]),
      .out_valid_o (l1_vn2o_valid[t]), .out_ready_i (l1_vn2o_ready[t]),
      .out_msg_o (l1_vn2o_msg[t])
    );

    mem_model #(.MEM_LINES (MEM_LINES), .LATENCY (MEM_LATENCY_P)) u_mem (
      .clk (clk), .rst_n (rst_n),
      .req_valid_i (mem_req_valid), .req_ready_o (mem_req_ready),
      .req_addr_i  (mem_req_addr), .req_we_i (mem_req_we),
      .req_wdata_i (mem_req_wdata),
      .resp_valid_o (mem_resp_valid), .resp_rdata_o (mem_resp_rdata)
    );
  end

  //---------------------------------------------------------------------------
  // VN0: every L1 -> the addressed directory bank.
  //---------------------------------------------------------------------------
  logic [NUM_TILES-1:0][NUM_TILES-1:0] vn0_sel;
  logic [NUM_TILES-1:0][NUM_TILES-1:0] vn0_grant;

  always_comb begin
    for (int unsigned d = 0; d < NUM_TILES; d++) begin
      for (int unsigned s = 0; s < NUM_TILES; s++) begin
        vn0_sel[d][s] = (l1_vn0_msg[s].dst == TILE_ID_W'(d));
      end
    end
    for (int unsigned s = 0; s < NUM_TILES; s++) begin
      l1_vn0_ready[s] = 1'b0;
      for (int unsigned d = 0; d < NUM_TILES; d++) begin
        if (vn0_grant[d][s]) l1_vn0_ready[s] = 1'b1;
      end
    end
  end

  for (genvar d = 0; d < int'(NUM_TILES); d++) begin : gen_vn0_mux
    msg_mux #(.N (NUM_TILES)) u_mux (
      .clk (clk), .rst_n (rst_n),
      .src_valid_i (l1_vn0_valid), .src_sel_i (vn0_sel[d]),
      .src_msg_i (l1_vn0_msg), .src_grant_o (vn0_grant[d]),
      .dst_valid_o (d_vn0_valid[d]), .dst_ready_i (d_vn0_ready[d]),
      .dst_msg_o (d_vn0_msg[d])
    );
  end

  //---------------------------------------------------------------------------
  // VN1: every directory -> the addressed L1.
  //---------------------------------------------------------------------------
  logic [NUM_TILES-1:0][NUM_TILES-1:0] vn1_sel;
  logic [NUM_TILES-1:0][NUM_TILES-1:0] vn1_grant;

  always_comb begin
    for (int unsigned d = 0; d < NUM_TILES; d++) begin
      for (int unsigned s = 0; s < NUM_TILES; s++) begin
        vn1_sel[d][s] = (d_vn1_msg[s].dst == TILE_ID_W'(d));
      end
    end
    for (int unsigned s = 0; s < NUM_TILES; s++) begin
      d_vn1_ready[s] = 1'b0;
      for (int unsigned d = 0; d < NUM_TILES; d++) begin
        if (vn1_grant[d][s]) d_vn1_ready[s] = 1'b1;
      end
    end
  end

  for (genvar d = 0; d < int'(NUM_TILES); d++) begin : gen_vn1_mux
    msg_mux #(.N (NUM_TILES)) u_mux (
      .clk (clk), .rst_n (rst_n),
      .src_valid_i (d_vn1_valid), .src_sel_i (vn1_sel[d]),
      .src_msg_i (d_vn1_msg), .src_grant_o (vn1_grant[d]),
      .dst_valid_o (l1_vn1_valid[d]), .dst_ready_i (l1_vn1_ready[d]),
      .dst_msg_o (l1_vn1_msg[d])
    );
  end

  //---------------------------------------------------------------------------
  // VN2: 2*NUM_TILES sources (directories then L1s) onto both L1 and directory
  // inputs. A VN2 message is for a directory exactly when it is WB-Data.
  //---------------------------------------------------------------------------
  localparam int unsigned VN2_SRC = 2 * NUM_TILES;

  logic     [VN2_SRC-1:0] vn2_src_valid;
  coh_msg_t [VN2_SRC-1:0] vn2_src_msg;
  logic     [VN2_SRC-1:0] vn2_to_l1_grant   [NUM_TILES];
  logic     [VN2_SRC-1:0] vn2_to_dir_grant  [NUM_TILES];

  always_comb begin
    for (int unsigned t = 0; t < NUM_TILES; t++) begin
      vn2_src_valid[t]             = d_vn2o_valid[t];
      vn2_src_msg[t]               = d_vn2o_msg[t];
      vn2_src_valid[NUM_TILES + t] = l1_vn2o_valid[t];
      vn2_src_msg[NUM_TILES + t]   = l1_vn2o_msg[t];
    end
  end

  logic [VN2_SRC-1:0] vn2_sel_l1  [NUM_TILES];
  logic [VN2_SRC-1:0] vn2_sel_dir [NUM_TILES];

  always_comb begin
    for (int unsigned d = 0; d < NUM_TILES; d++) begin
      for (int unsigned s = 0; s < VN2_SRC; s++) begin
        automatic logic for_dir = (vn2_src_msg[s].msg_type == MSG_WB_DATA);
        vn2_sel_l1[d][s]  = !for_dir && (vn2_src_msg[s].dst == TILE_ID_W'(d));
        vn2_sel_dir[d][s] =  for_dir && (vn2_src_msg[s].dst == TILE_ID_W'(d));
      end
    end
    for (int unsigned t = 0; t < NUM_TILES; t++) begin
      d_vn2o_ready[t]  = 1'b0;
      l1_vn2o_ready[t] = 1'b0;
      for (int unsigned d = 0; d < NUM_TILES; d++) begin
        if (vn2_to_l1_grant[d][t] || vn2_to_dir_grant[d][t]) begin
          d_vn2o_ready[t] = 1'b1;
        end
        if (vn2_to_l1_grant[d][NUM_TILES + t] || vn2_to_dir_grant[d][NUM_TILES + t]) begin
          l1_vn2o_ready[t] = 1'b1;
        end
      end
    end
  end

  for (genvar d = 0; d < int'(NUM_TILES); d++) begin : gen_vn2_mux
    msg_mux #(.N (VN2_SRC)) u_to_l1 (
      .clk (clk), .rst_n (rst_n),
      .src_valid_i (vn2_src_valid), .src_sel_i (vn2_sel_l1[d]),
      .src_msg_i (vn2_src_msg), .src_grant_o (vn2_to_l1_grant[d]),
      .dst_valid_o (l1_vn2i_valid[d]), .dst_ready_i (l1_vn2i_ready[d]),
      .dst_msg_o (l1_vn2i_msg[d])
    );
    msg_mux #(.N (VN2_SRC)) u_to_dir (
      .clk (clk), .rst_n (rst_n),
      .src_valid_i (vn2_src_valid), .src_sel_i (vn2_sel_dir[d]),
      .src_msg_i (vn2_src_msg), .src_grant_o (vn2_to_dir_grant[d]),
      .dst_valid_o (d_vn2i_valid[d]), .dst_ready_i (d_vn2i_ready[d]),
      .dst_msg_o (d_vn2i_msg[d])
    );
  end

endmodule : coh_direct_top
