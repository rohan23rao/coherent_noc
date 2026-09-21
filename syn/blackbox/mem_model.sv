//=============================================================================
// syn/blackbox/mem_model.sv
//
// The synthesis stand-in for rtl/mem/mem_model.sv.
//
// mem_model is a testbench component, not part of the design: it is a
// behavioural memory with a fixed-latency response, sitting where a real
// system would have a memory controller and DRAM. Synthesising it would
// produce a 256 kbit register file and a meaningless area number, and leaving
// it out would break the tile's port list.
//
// So: same ports, no body. Every path to and from the memory channel then ends
// at a black box boundary, which is exactly what it is.
//=============================================================================

module mem_model
  import coh_pkg::*;
// The parameter list is copied from rtl/mem/mem_model.sv verbatim, defaults
// included: LATENCY defaults to coh_pkg::MEM_LATENCY, and writing 8 here
// instead would leave that package parameter with no reader and turn a
// substitution artefact into a lint failure.
#(
  parameter  int unsigned MEM_LINES = 1024,
  parameter  int unsigned LATENCY   = MEM_LATENCY
) (
  input  logic                    clk,
  input  logic                    rst_n,
  input  logic                    req_valid_i,
  output logic                    req_ready_o,
  input  logic [LINE_ADDR_W-1:0]  req_addr_i,
  input  logic                    req_we_i,
  input  logic [LINE_W-1:0]       req_wdata_i,
  output logic                    resp_valid_o,
  output logic [LINE_W-1:0]       resp_rdata_o
);
  // Intentionally empty: this is a black box.
endmodule : mem_model
