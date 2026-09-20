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
  // Eligibility, computed for EVERY input VC before stage 1 runs.
  //
  // This is not an optimization. Stage 1 picks one VC per input port and its
  // round-robin pointer only advances when a grant is actually taken. If a VC
  // whose output port has no free VC in its vnet were allowed to win stage 1,
  // it would fail stage 2 every cycle, the pointer would never move, and every
  // other VC on that input port would be starved for as long as the blockage
  // lasted. A blocked VN0 would then stall VN1 and VN2 inside the allocator --
  // defeating the entire point of having separate virtual networks, and
  // deadlocking the moment the response that would clear the blockage is the
  // thing being starved. Bug B14.
  //
  // Masking the request instead means a blocked VC simply does not compete.
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0][VC_SEL_W-1:0] vc_cand;
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0]               vc_cand_valid;
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0]               eligible;

  always_comb begin
    for (int unsigned p = 0; p < NUM_PORTS; p++) begin
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        // The output VC is the SAME index as the input VC, never "the
        // lowest free one in this vnet". A packet therefore keeps its virtual
        // channel from injection to ejection, which makes each VC id an
        // independent XY-routed subnetwork -- deadlock-free on its own, and
        // first-in-first-out between any pair of tiles that share it.
        //
        // The FIFO property is the point. Choosing the lowest free VC gives
        // better channel utilisation and lets two messages from one sender to
        // one receiver arrive out of order, which the coherence protocol
        // cannot survive: a Put-Ack overtaking a forward retires the cache's
        // transaction before the forward lands. Bug B19, decision D22.
        vc_cand[p][v]       = VC_SEL_W'(v);
        vc_cand_valid[p][v] = !out_vc_busy_i[out_port_i[p][v]][VC_SEL_W'(v)];
        eligible[p][v] = req_i[p][v] && vc_cand_valid[p][v];
      end
    end
  end

  //---------------------------------------------------------------------------
  // Stage 1: one winning VC per input port, among the ELIGIBLE ones.
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
      .req_i       (eligible[p]),
      .take_i      (s1_take[p]),
      .gnt_o       (s1_gnt[p]),
      .gnt_valid_o (s1_valid[p]),
      .gnt_idx_o   (s1_vc[p])
    );
  end

  //---------------------------------------------------------------------------
  // The stage-1 winner's candidate, already computed above.
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][PORT_W-1:0]   cand_port;
  logic [NUM_PORTS-1:0][VC_SEL_W-1:0] cand_vc;
  logic [NUM_PORTS-1:0]               cand_valid;

  always_comb begin
    for (int unsigned p = 0; p < NUM_PORTS; p++) begin
      cand_port[p]  = out_port_i[p][s1_vc[p]];
      cand_vc[p]    = vc_cand[p][s1_vc[p]];
      cand_valid[p] = s1_valid[p] && vc_cand_valid[p][s1_vc[p]];
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

    // Stronger, and the one the coherence protocol depends on: a packet keeps
    // its VC index, not merely its vnet. That is what makes each VC id an
    // independent FIFO subnetwork and stops a Put-Ack overtaking a forward to
    // the same cache. Bug B19.
    a_same_vc : assert property (@(posedge clk) disable iff (!rst_n)
      (|grant_o[p]) |-> (grant_vc_o[p] == s1_vc[p]))
      else $error("vc_allocator: input port %0d moved a packet from VC %0d to VC %0d -- point-to-point order is no longer guaranteed", p, s1_vc[p], grant_vc_o[p]);
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
