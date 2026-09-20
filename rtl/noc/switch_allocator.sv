//=============================================================================
// switch_allocator.sv
//
// Separable input-first switch allocator, two round-robin stages.
//   Stage 1: one VC per input port.
//   Stage 2: one input port per output port.
//
// Interfaces: per-input-VC request (already masked by downstream credit by the
// router) and its output port; grants back to the input units, plus the
// crossbar select for each output port.
// COMBINATIONAL -- same pipeline stage as VC allocation.
//
// The one non-obvious thing: requests arriving here are already credit-masked.
// Arbitrating first and checking credit afterwards would waste the grant -- the
// winner could not use it, and the output port would idle for a cycle even
// though another input was ready. Masking first costs nothing and keeps the
// allocator's grant rate equal to its useful-work rate.
//=============================================================================

module switch_allocator
  import coh_pkg::*;
(
  input  logic                                                clk,
  input  logic                                                rst_n,

  input  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0]              req_i,
  input  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0][PORT_W-1:0]  out_port_i,

  output logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0]              grant_o,

  // Crossbar control, one entry per output port.
  output logic [NUM_PORTS-1:0]                                xbar_valid_o,
  output logic [NUM_PORTS-1:0][PORT_W-1:0]                    xbar_sel_o
);

  //---------------------------------------------------------------------------
  // Stage 1: one VC per input port.
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0] s1_gnt;
  logic [NUM_PORTS-1:0]                   s1_valid;
  logic [NUM_PORTS-1:0][VC_SEL_W-1:0]     s1_vc;
  logic [NUM_PORTS-1:0]                   s1_take;
  logic [NUM_PORTS-1:0][PORT_W-1:0]       s1_port;

  for (genvar p = 0; p < int'(NUM_PORTS); p++) begin : gen_s1
    rr_arbiter #(
      .N (VCS_PER_PORT)
    ) u_s1 (
      .clk         (clk),
      .rst_n       (rst_n),
      .req_i       (req_i[p]),
      .take_i      (s1_take[p]),
      .gnt_o       (s1_gnt[p]),
      .gnt_valid_o (s1_valid[p]),
      .gnt_idx_o   (s1_vc[p])
    );
    assign s1_port[p] = out_port_i[p][s1_vc[p]];
  end

  //---------------------------------------------------------------------------
  // Stage 2: one input port per output port.
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][NUM_PORTS-1:0] s2_req;
  logic [NUM_PORTS-1:0][NUM_PORTS-1:0] s2_gnt;
  logic [NUM_PORTS-1:0]                s2_valid;
  logic [NUM_PORTS-1:0][PORT_W-1:0]    s2_idx;

  always_comb begin
    for (int unsigned o = 0; o < NUM_PORTS; o++) begin
      for (int unsigned p = 0; p < NUM_PORTS; p++) begin
        s2_req[o][p] = s1_valid[p] && (s1_port[p] == PORT_W'(o));
      end
    end
  end

  for (genvar o = 0; o < int'(NUM_PORTS); o++) begin : gen_s2
    rr_arbiter #(
      .N (NUM_PORTS)
    ) u_s2 (
      .clk         (clk),
      .rst_n       (rst_n),
      .req_i       (s2_req[o]),
      .take_i      (1'b1),
      .gnt_o       (s2_gnt[o]),
      .gnt_valid_o (s2_valid[o]),
      .gnt_idx_o   (s2_idx[o])
    );
  end

  //---------------------------------------------------------------------------
  // Collect grants and drive the crossbar.
  //---------------------------------------------------------------------------
  always_comb begin
    grant_o      = '0;
    s1_take      = '0;
    xbar_valid_o = '0;
    xbar_sel_o   = '0;

    for (int unsigned o = 0; o < NUM_PORTS; o++) begin
      xbar_valid_o[o] = s2_valid[o];
      xbar_sel_o[o]   = s2_idx[o];
      for (int unsigned p = 0; p < NUM_PORTS; p++) begin
        if (s2_gnt[o][p]) begin
          grant_o[p] = s1_gnt[p];
          s1_take[p] = 1'b1;
        end
      end
    end
  end

`ifndef SYNTHESIS
  for (genvar p = 0; p < int'(NUM_PORTS); p++) begin : gen_sa_in_asserts
    a_grant_onehot : assert property (@(posedge clk) disable iff (!rst_n)
      $onehot0(grant_o[p]))
      else $error("switch_allocator: input port %0d granted more than one VC", p);

    a_grant_implies_req : assert property (@(posedge clk) disable iff (!rst_n)
      (|grant_o[p]) |-> ((grant_o[p] & req_i[p]) == grant_o[p]))
      else $error("switch_allocator: input port %0d granted a VC that was not requesting", p);
  end

  for (genvar o = 0; o < int'(NUM_PORTS); o++) begin : gen_sa_out_asserts
    // The crossbar does no arbitration of its own and trusts this property.
    a_one_input_per_output : assert property (@(posedge clk) disable iff (!rst_n)
      $onehot0(s2_gnt[o]))
      else $error("switch_allocator: output port %0d granted to more than one input", o);
  end
`endif

endmodule : switch_allocator
