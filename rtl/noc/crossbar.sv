//=============================================================================
// crossbar.sv
//
// NUM_PORTS x NUM_PORTS flit crossbar. Each output port selects the flit of the
// input port that won switch allocation for it this cycle.
//
// Interfaces: sel_valid_i / sel_port_i per output port (from the switch
// allocator), in_flit_i per input port; out_flit_o / out_valid_o per output.
// COMBINATIONAL -- the router registers the outputs at the ST/LT boundary.
//
// The one non-obvious thing: there is no arbitration here at all. The crossbar
// is pure muxing and trusts that the switch allocator granted at most one input
// per output; the assertion for that lives in switch_allocator, where the
// property is actually decided. Duplicating it here would test the wiring, not
// the allocator.
//=============================================================================

module crossbar
  import coh_pkg::*;
(
  input  logic  [NUM_PORTS-1:0]                 sel_valid_i,
  input  logic  [NUM_PORTS-1:0][PORT_W-1:0]     sel_port_i,
  input  flit_t [NUM_PORTS-1:0]                 in_flit_i,
  output logic  [NUM_PORTS-1:0]                 out_valid_o,
  output flit_t [NUM_PORTS-1:0]                 out_flit_o
);

  always_comb begin
    for (int unsigned o = 0; o < NUM_PORTS; o++) begin
      out_valid_o[o] = sel_valid_i[o];
      out_flit_o[o]  = in_flit_i[sel_port_i[o]];
    end
  end

endmodule : crossbar
