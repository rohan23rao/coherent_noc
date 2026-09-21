//=============================================================================
// syn/blackbox/sram_1rw.sv
//
// The synthesis stand-in for rtl/lib/sram_1rw.sv. Same module name, same
// parameters, same ports, no body -- so Design Compiler treats every array in
// the design as an unresolved reference and leaves a black box in the netlist.
//
// Why this exists rather than synthesising the behavioural array:
//
//   The behavioural model is a `logic [W-1:0] mem_q [D]`, which Design Compiler
//   will happily turn into flip-flops. For this design that is 4 KB of L1 data,
//   16 KB of L2 data and both tag arrays -- about 170 kbit per tile, 680 kbit
//   for the system. A flop-based number is not wrong, it is just an answer to a
//   question nobody asked: no one builds a cache out of standard-cell flops.
//   The area that means something is the control logic's, and the array's area
//   and access time come from the foundry memory compiler.
//
//   syn/constraints/common.sdc budgets half the clock period on each side of
//   every black box so the logic feeding the address and consuming the read
//   data is still timed. What is NOT modelled is the array's own access time.
//   That is stated in every report this flow produces, and it is the one number
//   this run cannot give you.
//
// To synthesise the arrays as flops instead -- worth doing once for the tag
// arrays, which are small enough to be real -- run with SYN_SRAM=flops.
//
// This file is NEVER read by the simulation flow. sim/Makefile's file list
// names rtl/lib/sram_1rw.sv; syn/scripts/synth.tcl substitutes this one.
//=============================================================================

module sram_1rw #(
  parameter  int unsigned WIDTH = 32,
  parameter  int unsigned DEPTH = 64,
  localparam int unsigned ADDR_BITS = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic                    clk,
  input  logic                    en_i,
  input  logic                    we_i,
  input  logic [ADDR_BITS-1:0]    addr_i,
  input  logic [WIDTH-1:0]        wdata_i,
  output logic [WIDTH-1:0]        rdata_o
);
  // Intentionally empty: this is a black box.
endmodule : sram_1rw
