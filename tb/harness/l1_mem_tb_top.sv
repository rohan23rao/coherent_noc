//=============================================================================
// l1_mem_tb_top.sv  (TESTBENCH SCAFFOLDING -- not part of rtl/)
//
// Wires one l1_cache directly to one mem_model so the Phase 4 tests can drive
// the core interface and nothing else. It exists because the real tile top does
// not arrive until the network phases, and bringing the L1 up against a
// directly-attached memory is what keeps a Phase 4 cache bug from being
// confused with a Phase 8 network bug later.
//
// The memory parameters are exposed so tests can shrink the modelled window and
// shorten the latency without touching rtl/.
//=============================================================================

module l1_mem_tb_top
  import coh_pkg::*;
#(
  parameter int unsigned MEM_LINES     = 256,
  parameter int unsigned MEM_LATENCY_P = 6
) (
  input  logic                        clk,
  input  logic                        rst_n,

  input  logic                        core_req_valid_i,
  output logic                        core_req_ready_o,
  input  core_op_e                    core_op_i,
  input  logic [ADDR_W-1:0]           core_addr_i,
  input  logic [WORD_W-1:0]           core_wdata_i,
  input  logic [BE_W-1:0]             core_be_i,
  input  logic [CORE_TAG_W-1:0]       core_tag_i,

  output logic                        core_resp_valid_o,
  output logic [CORE_TAG_W-1:0]       core_resp_tag_o,
  output logic [WORD_W-1:0]           core_resp_rdata_o,

  input  logic [L1_IDX_W-1:0]         dbg_set_i,
  input  logic [L1_WAY_W-1:0]         dbg_way_i,
  output l1_state_e                   dbg_state_o,
  output logic [L1_TAG_W-1:0]         dbg_tag_o,
  output logic [MSHR_ENTRIES-1:0]     dbg_mshr_valid_o
);

  logic                   mem_req_valid;
  logic                   mem_req_ready;
  logic [LINE_ADDR_W-1:0] mem_req_addr;
  logic                   mem_req_we;
  logic [LINE_W-1:0]      mem_req_wdata;
  logic                   mem_resp_valid;
  logic [LINE_W-1:0]      mem_resp_rdata;

  l1_cache u_l1 (
    .clk               (clk),
    .rst_n             (rst_n),
    .core_req_valid_i  (core_req_valid_i),
    .core_req_ready_o  (core_req_ready_o),
    .core_op_i         (core_op_i),
    .core_addr_i       (core_addr_i),
    .core_wdata_i      (core_wdata_i),
    .core_be_i         (core_be_i),
    .core_tag_i        (core_tag_i),
    .core_resp_valid_o (core_resp_valid_o),
    .core_resp_tag_o   (core_resp_tag_o),
    .core_resp_rdata_o (core_resp_rdata_o),
    .mem_req_valid_o   (mem_req_valid),
    .mem_req_ready_i   (mem_req_ready),
    .mem_req_addr_o    (mem_req_addr),
    .mem_req_we_o      (mem_req_we),
    .mem_req_wdata_o   (mem_req_wdata),
    .mem_resp_valid_i  (mem_resp_valid),
    .mem_resp_rdata_i  (mem_resp_rdata),
    .dbg_set_i         (dbg_set_i),
    .dbg_way_i         (dbg_way_i),
    .dbg_state_o       (dbg_state_o),
    .dbg_tag_o         (dbg_tag_o),
    .dbg_mshr_valid_o  (dbg_mshr_valid_o)
  );

  mem_model #(
    .MEM_LINES (MEM_LINES),
    .LATENCY   (MEM_LATENCY_P)
  ) u_mem (
    .clk          (clk),
    .rst_n        (rst_n),
    .req_valid_i  (mem_req_valid),
    .req_ready_o  (mem_req_ready),
    .req_addr_i   (mem_req_addr),
    .req_we_i     (mem_req_we),
    .req_wdata_i  (mem_req_wdata),
    .resp_valid_o (mem_resp_valid),
    .resp_rdata_o (mem_resp_rdata)
  );

endmodule : l1_mem_tb_top
