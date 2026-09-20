//=============================================================================
// vc_allocator.sv
//
// Separable input-first VC allocator, two round-robin stages.
//   Stage 1: one winning input VC per input port.
//   Stage 2: one winner per output VC, arbitrated across input ports.
//
// Interfaces: per-input-VC request and desired output port; the set of busy
// output VCs; grants back to the input units.
// COMBINATIONAL -- it shares the VA/SA pipeline stage.
//
// The one non-obvious thing: a request may only be offered output VCs inside
// its own virtual network. A packet that changed vnet would break the
// dependency ordering that makes three virtual networks sufficient -- a
// response sitting in a request-class buffer can be blocked by a request, and
// the acyclic message-dependency argument collapses. It is cheaper to enforce
// here than to detect later, so the candidate search is restricted by
// construction and asserted as well.
//=============================================================================

module vc_allocator
  import coh_pkg::*;
(
  input  logic                                                  clk,
  input  logic                                                  rst_n,

  // Per input port, per input VC.
  input  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]               req_i,
  input  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0][PORT_W-1:0]   out_port_i,

  // Output VC occupancy, indexed [output port][output VC].
  input  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]               out_vc_busy_i,

  // Grants back to the input units.
  output logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]               grant_o,
  output logic  [NUM_PORTS-1:0][VC_SEL_W-1:0]                   grant_vc_o,

  // Which output VCs were allocated this cycle, for the router to mark busy.
  output logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]               alloc_o
);

  //---------------------------------------------------------------------------
  // Stage 1: one winning VC per input port.
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0] s1_gnt;
  logic [NUM_PORTS-1:0]                   s1_valid;
  logic [NUM_PORTS-1:0][VC_SEL_W-1:0]     s1_vc;

  // The pointer must only advance when the stage-2 arbitration actually takes
  // the winner, or an input VC that keeps losing stage 2 would be rotated past
  // and starved.
  logic [NUM_PORTS-1:0]                   s1_take;

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
  end

  //---------------------------------------------------------------------------
  // Candidate output VC for each input port's stage-1 winner: the lowest free
  // VC inside the winner's own vnet on its desired output port.
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][PORT_W-1:0]   cand_port;
  logic [NUM_PORTS-1:0][VC_SEL_W-1:0] cand_vc;
  logic [NUM_PORTS-1:0]               cand_valid;

  always_comb begin
    for (int unsigned p = 0; p < NUM_PORTS; p++) begin
      automatic vnet_e vn = vc_to_vnet(s1_vc[p]);
      cand_port[p]  = out_port_i[p][s1_vc[p]];
      cand_vc[p]    = '0;
      cand_valid[p] = 1'b0;
      if (s1_valid[p]) begin
        for (int unsigned k = 0; k < VCS_PER_VNET; k++) begin
          automatic logic [VC_SEL_W-1:0] idx = vc_index(vn, VC_ID_W'(k));
          if (!cand_valid[p] && !out_vc_busy_i[cand_port[p]][idx]) begin
            cand_vc[p]    = idx;
            cand_valid[p] = 1'b1;
          end
        end
      end
    end
  end

  //---------------------------------------------------------------------------
  // Stage 2: one winner per output VC, arbitrated across input ports.
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0][NUM_PORTS-1:0] s2_req;
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0][NUM_PORTS-1:0] s2_gnt;

  always_comb begin
    for (int unsigned o = 0; o < NUM_PORTS; o++) begin
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        for (int unsigned p = 0; p < NUM_PORTS; p++) begin
          s2_req[o][v][p] = cand_valid[p]
                            && (cand_port[p] == PORT_W'(o))
                            && (cand_vc[p]   == VC_SEL_W'(v));
        end
      end
    end
  end

  for (genvar o = 0; o < int'(NUM_PORTS); o++) begin : gen_s2_port
    for (genvar v = 0; v < int'(VCS_PER_PORT); v++) begin : gen_s2_vc
      rr_arbiter #(
        .N (NUM_PORTS)
      ) u_s2 (
        .clk         (clk),
        .rst_n       (rst_n),
        .req_i       (s2_req[o][v]),
        .take_i      (1'b1),
        .gnt_o       (s2_gnt[o][v]),
        .gnt_valid_o (),
        .gnt_idx_o   ()
      );
    end
  end

  //---------------------------------------------------------------------------
  // Collect grants.
  //---------------------------------------------------------------------------
  always_comb begin
    grant_o    = '0;
    grant_vc_o = '0;
    alloc_o    = '0;
    s1_take    = '0;

    for (int unsigned p = 0; p < NUM_PORTS; p++) begin
      for (int unsigned o = 0; o < NUM_PORTS; o++) begin
        for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
          if (s2_gnt[o][v][p]) begin
            grant_o[p]    = s1_gnt[p];
            grant_vc_o[p] = VC_SEL_W'(v);
            alloc_o[o][v] = 1'b1;
            s1_take[p]    = 1'b1;
          end
        end
      end
    end
  end

`ifndef SYNTHESIS
  for (genvar p = 0; p < int'(NUM_PORTS); p++) begin : gen_va_asserts
    a_grant_onehot : assert property (@(posedge clk) disable iff (!rst_n)
      $onehot0(grant_o[p]))
      else $error("vc_allocator: input port %0d granted more than one VC", p);

    a_grant_implies_req : assert property (@(posedge clk) disable iff (!rst_n)
      (|grant_o[p]) |-> ((grant_o[p] & req_i[p]) == grant_o[p]))
      else $error("vc_allocator: input port %0d granted a VC that was not requesting", p);

    a_same_vnet : assert property (@(posedge clk) disable iff (!rst_n)
      (|grant_o[p]) |-> (vc_to_vnet(grant_vc_o[p]) == vc_to_vnet(s1_vc[p])))
      else $error("vc_allocator: input port %0d would move a packet out of its vnet", p);
  end

  for (genvar o = 0; o < int'(NUM_PORTS); o++) begin : gen_va_out_asserts
    for (genvar v = 0; v < int'(VCS_PER_PORT); v++) begin : gen_va_out_vc
      a_no_busy_realloc : assert property (@(posedge clk) disable iff (!rst_n)
        alloc_o[o][v] |-> !out_vc_busy_i[o][v])
        else $error("vc_allocator: allocated output port %0d VC %0d while it was still busy", o, v);

      a_s2_onehot : assert property (@(posedge clk) disable iff (!rst_n)
        $onehot0(s2_gnt[o][v]))
        else $error("vc_allocator: output port %0d VC %0d granted to more than one input", o, v);
    end
  end
`endif

endmodule : vc_allocator
