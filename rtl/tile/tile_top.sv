//=============================================================================
// tile_top.sv
//
// One tile: an L1 data cache, the directory bank that is home for a quarter of
// the address space, the memory behind that bank, and the network interface
// that connects both to the tile's router.
//
// Interfaces: clk, rst_n, tile_id_i; the core request/response channels; the
// router's local port; debug buses; and the per-virtual-network message hold
// controls.
//
// The one non-obvious thing: the message path between the caches and the NIC
// is REGISTERED, through msg_hold, and the number of cycles it holds is an
// input. At zero it is an ordinary pipeline register on the NIC boundary,
// which is where a register belongs anyway. Above zero it lets a directed test
// force an exact interleaving between two sources without modifying anything
// -- the same hardware in both cases, so the design that is verified is the
// design that ships. See docs/decisions.md D16.
//=============================================================================

module tile_top
  import coh_pkg::*;
#(
  parameter int unsigned TILE_ID   = 0,
  parameter int unsigned MEM_LINES = 1024,
  parameter int unsigned MEM_LAT   = 8,
  parameter bit          ENABLE_E  = 1'b1,
  parameter int unsigned TBE_TO    = TBE_TIMEOUT
) (
  input  logic                      clk,
  input  logic                      rst_n,

  input  logic                      core_req_valid_i,
  output logic                      core_req_ready_o,
  input  core_op_e                  core_op_i,
  input  logic [ADDR_W-1:0]         core_addr_i,
  input  logic [WORD_W-1:0]         core_wdata_i,
  input  logic [BE_W-1:0]           core_be_i,
  input  logic [CORE_TAG_W-1:0]     core_tag_i,

  output logic                      core_resp_valid_o,
  output logic [CORE_TAG_W-1:0]     core_resp_tag_o,
  output logic [WORD_W-1:0]         core_resp_rdata_o,

  // Router local port.
  output logic                      flit_valid_o,
  output flit_t                     flit_o,
  input  logic                      credit_valid_i,
  input  logic [VC_SEL_W-1:0]       credit_vc_i,
  input  logic                      credit_tail_i,
  input  logic                      flit_valid_i,
  input  flit_t                     flit_i,
  output logic                      credit_valid_o,
  output logic [VC_SEL_W-1:0]       credit_vc_o,
  output logic                      credit_tail_o,

  // Message-path hold, in cycles, per virtual network.
  input  logic [7:0]                hold_vn0_i,
  input  logic [7:0]                hold_vn1_i,
  input  logic [7:0]                hold_vn2_i,
  input  logic [7:0]                hold_vn2_dir_i,

  // Debug.
  input  logic [L1_IDX_W-1:0]       dbg_set_i,
  output logic [L1_WAYS-1:0][3:0]   dbg_state_o,
  output logic [L1_WAYS-1:0][L1_TAG_W-1:0] dbg_tag_o,
  output logic [MSHR_ENTRIES-1:0]   dbg_mshr_valid_o,
  input  logic [L2_IDX_W-1:0]       dbg_dir_set_i,
  input  logic [L2_WAY_W-1:0]       dbg_dir_way_i,
  output dir_meta_t                 dbg_dir_meta_o
);

  // Raw module ports, before the registered hold.
  logic     l1_vn0_rv, l1_vn0_rr;  coh_msg_t l1_vn0_rm;
  logic     l1_vn2_rv, l1_vn2_rr;  coh_msg_t l1_vn2_rm;
  logic     d_vn1_rv,  d_vn1_rr;   coh_msg_t d_vn1_rm;
  logic     d_vn2_rv,  d_vn2_rr;   coh_msg_t d_vn2_rm;

  // Held versions, which the NIC sees.
  logic     l1_vn0_v, l1_vn0_r;    coh_msg_t l1_vn0_m;
  logic     l1_vn2_v, l1_vn2_r;    coh_msg_t l1_vn2_m;
  logic     d_vn1_v,  d_vn1_r;     coh_msg_t d_vn1_m;
  logic     d_vn2_v,  d_vn2_r;     coh_msg_t d_vn2_m;

  // Inbound from the NIC.
  logic     l1_vn1_iv, l1_vn1_ir;  coh_msg_t l1_vn1_im;
  logic     l1_vn2_iv, l1_vn2_ir;  coh_msg_t l1_vn2_im;
  logic     d_vn0_iv,  d_vn0_ir;   coh_msg_t d_vn0_im;
  logic     d_vn2_iv,  d_vn2_ir;   coh_msg_t d_vn2_im;

  l1_cache u_l1 (
    .clk (clk), .rst_n (rst_n), .tile_id_i (TILE_ID_W'(TILE_ID)),
    .core_req_valid_i (core_req_valid_i), .core_req_ready_o (core_req_ready_o),
    .core_op_i (core_op_i), .core_addr_i (core_addr_i),
    .core_wdata_i (core_wdata_i), .core_be_i (core_be_i),
    .core_tag_i (core_tag_i),
    .core_resp_valid_o (core_resp_valid_o), .core_resp_tag_o (core_resp_tag_o),
    .core_resp_rdata_o (core_resp_rdata_o),
    .vn0_valid_o (l1_vn0_rv), .vn0_ready_i (l1_vn0_rr), .vn0_msg_o (l1_vn0_rm),
    .vn1_valid_i (l1_vn1_iv), .vn1_ready_o (l1_vn1_ir), .vn1_msg_i (l1_vn1_im),
    .vn2_valid_i (l1_vn2_iv), .vn2_ready_o (l1_vn2_ir), .vn2_msg_i (l1_vn2_im),
    .vn2_valid_o (l1_vn2_rv), .vn2_ready_i (l1_vn2_rr), .vn2_msg_o (l1_vn2_rm),
    .dbg_set_i (dbg_set_i), .dbg_state_o (dbg_state_o), .dbg_tag_o (dbg_tag_o),
    .dbg_mshr_valid_o (dbg_mshr_valid_o)
  );

  logic                   mem_rv, mem_rr, mem_we, mem_pv;
  logic [LINE_ADDR_W-1:0] mem_addr;
  logic [LINE_W-1:0]      mem_wd, mem_rd;

  dir_ctrl #(.BANK_ID (TILE_ID), .ENABLE_E (ENABLE_E),
             .TBE_TIMEOUT_P (TBE_TO)) u_dir (
    .clk (clk), .rst_n (rst_n),
    .vn0_valid_i (d_vn0_iv), .vn0_ready_o (d_vn0_ir), .vn0_msg_i (d_vn0_im),
    .vn2_valid_i (d_vn2_iv), .vn2_ready_o (d_vn2_ir), .vn2_msg_i (d_vn2_im),
    .vn1_valid_o (d_vn1_rv), .vn1_ready_i (d_vn1_rr), .vn1_msg_o (d_vn1_rm),
    .vn2_valid_o (d_vn2_rv), .vn2_ready_i (d_vn2_rr), .vn2_msg_o (d_vn2_rm),
    .mem_req_valid_o (mem_rv), .mem_req_ready_i (mem_rr),
    .mem_req_addr_o (mem_addr), .mem_req_we_o (mem_we), .mem_req_wdata_o (mem_wd),
    .mem_resp_valid_i (mem_pv), .mem_resp_rdata_i (mem_rd),
    .dbg_set_i (dbg_dir_set_i), .dbg_way_i (dbg_dir_way_i),
    .dbg_meta_o (dbg_dir_meta_o)
  );

  mem_model #(.MEM_LINES (MEM_LINES), .LATENCY (MEM_LAT)) u_mem (
    .clk (clk), .rst_n (rst_n),
    .req_valid_i (mem_rv), .req_ready_o (mem_rr), .req_addr_i (mem_addr),
    .req_we_i (mem_we), .req_wdata_i (mem_wd),
    .resp_valid_o (mem_pv), .resp_rdata_o (mem_rd)
  );

  msg_hold u_hold_vn0 (
    .clk (clk), .rst_n (rst_n), .hold_i (hold_vn0_i),
    .in_valid_i (l1_vn0_rv), .in_ready_o (l1_vn0_rr), .in_msg_i (l1_vn0_rm),
    .out_valid_o (l1_vn0_v), .out_ready_i (l1_vn0_r), .out_msg_o (l1_vn0_m)
  );
  msg_hold u_hold_vn1 (
    .clk (clk), .rst_n (rst_n), .hold_i (hold_vn1_i),
    .in_valid_i (d_vn1_rv), .in_ready_o (d_vn1_rr), .in_msg_i (d_vn1_rm),
    .out_valid_o (d_vn1_v), .out_ready_i (d_vn1_r), .out_msg_o (d_vn1_m)
  );
  msg_hold u_hold_vn2_l1 (
    .clk (clk), .rst_n (rst_n), .hold_i (hold_vn2_i),
    .in_valid_i (l1_vn2_rv), .in_ready_o (l1_vn2_rr), .in_msg_i (l1_vn2_rm),
    .out_valid_o (l1_vn2_v), .out_ready_i (l1_vn2_r), .out_msg_o (l1_vn2_m)
  );
  // The directory's responses get their own hold, separate from the cache's.
  // Both are VN2, but a race that needs Data+AckCount to arrive AFTER the
  // Inv-Acks it counts (R1) has to delay the directory without delaying the
  // sharers, and one control for the pair cannot express that.
  msg_hold u_hold_vn2_dir (
    .clk (clk), .rst_n (rst_n), .hold_i (hold_vn2_dir_i),
    .in_valid_i (d_vn2_rv), .in_ready_o (d_vn2_rr), .in_msg_i (d_vn2_rm),
    .out_valid_o (d_vn2_v), .out_ready_i (d_vn2_r), .out_msg_o (d_vn2_m)
  );

  tile_nic #(.TILE_ID (TILE_ID)) u_nic (
    .clk (clk), .rst_n (rst_n),
    .l1_vn0_valid_i (l1_vn0_v), .l1_vn0_ready_o (l1_vn0_r), .l1_vn0_msg_i (l1_vn0_m),
    .l1_vn2_valid_i (l1_vn2_v), .l1_vn2_ready_o (l1_vn2_r), .l1_vn2_msg_i (l1_vn2_m),
    .l1_vn1_valid_o (l1_vn1_iv), .l1_vn1_ready_i (l1_vn1_ir), .l1_vn1_msg_o (l1_vn1_im),
    .l1_vn2_valid_o (l1_vn2_iv), .l1_vn2_ready_i (l1_vn2_ir), .l1_vn2_msg_o (l1_vn2_im),
    .dir_vn1_valid_i (d_vn1_v), .dir_vn1_ready_o (d_vn1_r), .dir_vn1_msg_i (d_vn1_m),
    .dir_vn2_valid_i (d_vn2_v), .dir_vn2_ready_o (d_vn2_r), .dir_vn2_msg_i (d_vn2_m),
    .dir_vn0_valid_o (d_vn0_iv), .dir_vn0_ready_i (d_vn0_ir), .dir_vn0_msg_o (d_vn0_im),
    .dir_vn2_valid_o (d_vn2_iv), .dir_vn2_ready_i (d_vn2_ir), .dir_vn2_msg_o (d_vn2_im),
    .flit_valid_o (flit_valid_o), .flit_o (flit_o),
    .credit_valid_i (credit_valid_i), .credit_vc_i (credit_vc_i),
    .credit_tail_i (credit_tail_i),
    .flit_valid_i (flit_valid_i), .flit_i (flit_i),
    .credit_valid_o (credit_valid_o), .credit_vc_o (credit_vc_o),
    .credit_tail_o (credit_tail_o)
  );

endmodule : tile_top
