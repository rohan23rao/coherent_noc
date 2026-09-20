//=============================================================================
// input_unit.sv
//
// One router input port: VCS_PER_PORT virtual channels, each a VC_DEPTH-flit
// buffer, plus the per-VC state that drives VC and switch allocation.
//
// Interfaces: clk, rst_n; the incoming link (flit_valid_i/flit_i) and the
// credit return to the upstream router; the VA request/grant pair; the SA
// request/grant pair and the selected flit.
//
// va_req_o, sa_req_o, vc_out_port_o, vc_out_vc_o and sa_flit_o are
// COMBINATIONAL: the allocators live in the same pipeline stage and the router
// registers the result at the crossbar output. Everything else, including the
// credit return, is registered.
//
// The one non-obvious thing: an input VC returns to IDLE when the *tail is read
// out*, and the upstream is told via credit_tail_o so that it frees the
// corresponding output VC only then. Freeing the output VC when the tail is
// merely *sent* -- the more common choice -- would let the upstream allocate
// that VC to a new packet whose head could arrive while this VC still holds the
// previous packet's flits, silently overwriting the routing state computed at
// buffer-write time. See docs/decisions.md D9.
//=============================================================================

module input_unit
  import coh_pkg::*;
#(
  parameter int unsigned MY_X = 0,
  parameter int unsigned MY_Y = 0
) (
  input  logic                                    clk,
  input  logic                                    rst_n,

  // Incoming link from the upstream router or local port.
  input  logic                                    flit_valid_i,
  input  flit_t                                   flit_i,

  // Credit return to the upstream router. At most one VC is read per cycle, so
  // one credit per cycle is sufficient.
  output logic                                    credit_valid_o,
  output logic [VC_SEL_W-1:0]                     credit_vc_o,
  output logic                                    credit_tail_o,

  // VC allocation. At most one VC per input port may win per cycle, so the
  // grant carries a single output-VC id.
  output logic [VCS_PER_PORT-1:0]                 va_req_o,
  output logic [VCS_PER_PORT-1:0][PORT_W-1:0]     vc_out_port_o,
  input  logic [VCS_PER_PORT-1:0]                 va_grant_i,
  input  logic [VC_SEL_W-1:0]                     va_grant_vc_i,

  // Switch allocation.
  output logic [VCS_PER_PORT-1:0]                 sa_req_o,
  output logic [VCS_PER_PORT-1:0][VC_SEL_W-1:0]   vc_out_vc_o,
  input  logic [VCS_PER_PORT-1:0]                 sa_grant_i,
  output flit_t                                   sa_flit_o
);

  //---------------------------------------------------------------------------
  // Per-VC buffers
  //---------------------------------------------------------------------------
  logic [VCS_PER_PORT-1:0]              buf_wr_valid;
  logic [VCS_PER_PORT-1:0]              buf_wr_ready;
  logic [VCS_PER_PORT-1:0]              buf_rd_valid;
  logic [VCS_PER_PORT-1:0]              buf_rd_ready;
  flit_t                                buf_rd_flit [VCS_PER_PORT];

  logic [VC_SEL_W-1:0]                  in_vc_idx;
  assign in_vc_idx = vc_index(flit_i.vnet, flit_i.vc_id);

  for (genvar v = 0; v < int'(VCS_PER_PORT); v++) begin : gen_vc_buf
    logic [FLIT_W-1:0] rd_data;

    assign buf_wr_valid[v] = flit_valid_i && (in_vc_idx == VC_SEL_W'(v));
    assign buf_rd_flit[v]  = flit_t'(rd_data);

    fifo #(
      .WIDTH (FLIT_W),
      .DEPTH (VC_DEPTH)
    ) u_vc_buf (
      .clk        (clk),
      .rst_n      (rst_n),
      .wr_valid_i (buf_wr_valid[v]),
      .wr_ready_o (buf_wr_ready[v]),
      .wr_data_i  (FLIT_W'(flit_i)),
      .rd_valid_o (buf_rd_valid[v]),
      .rd_ready_i (buf_rd_ready[v]),
      .rd_data_o  (rd_data),
      .count_o    (),
      .empty_o    (),
      .full_o     ()
    );
  end

  //---------------------------------------------------------------------------
  // Route compute, on the head flit only, at buffer-write time.
  //---------------------------------------------------------------------------
  port_e rc_port;

  route_compute #(
    .MY_X (MY_X),
    .MY_Y (MY_Y)
  ) u_route_compute (
    .dst_x_i    (flit_i.dst_x),
    .dst_y_i    (flit_i.dst_y),
    .out_port_o (rc_port)
  );

  //---------------------------------------------------------------------------
  // Per-VC state
  //---------------------------------------------------------------------------
  vc_state_e                  vc_state_q   [VCS_PER_PORT];
  logic [PORT_W-1:0]          vc_out_port_q[VCS_PER_PORT];
  logic [VC_SEL_W-1:0]        vc_out_vc_q  [VCS_PER_PORT];

  logic [VCS_PER_PORT-1:0]    head_write;
  logic [VCS_PER_PORT-1:0]    pop;

  always_comb begin
    for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
      head_write[v] = buf_wr_valid[v] && flit_i.head;
      pop[v]        = sa_grant_i[v];
      va_req_o[v]   = (vc_state_q[v] == VC_ROUTED);
      // A VC may request the switch once it owns an output VC and has a flit.
      // Downstream credit is applied by the router, which owns the counters.
      sa_req_o[v]   = ((vc_state_q[v] == VC_ALLOC) || (vc_state_q[v] == VC_ACTIVE))
                      && buf_rd_valid[v];
      vc_out_port_o[v] = vc_out_port_q[v];
      vc_out_vc_o[v]   = vc_out_vc_q[v];
      buf_rd_ready[v]  = pop[v];
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        vc_state_q[v]    <= VC_IDLE;
        vc_out_port_q[v] <= '0;
        vc_out_vc_q[v]   <= '0;
      end
    end else begin
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        // Buffer write of a head: latch the route.
        if (head_write[v]) begin
          vc_state_q[v]    <= VC_ROUTED;
          vc_out_port_q[v] <= PORT_W'(rc_port);
        end

        // VC allocation grant.
        if (va_grant_i[v]) begin
          vc_state_q[v]  <= VC_ALLOC;
          vc_out_vc_q[v] <= va_grant_vc_i;
        end

        // Switch grant: a flit departs.
        if (pop[v]) begin
          if (buf_rd_flit[v].tail) begin
            vc_state_q[v] <= VC_IDLE;
          end else begin
            vc_state_q[v] <= VC_ACTIVE;
          end
        end
      end
    end
  end

  //---------------------------------------------------------------------------
  // Selected flit (combinational -- it feeds the crossbar in the same stage)
  //---------------------------------------------------------------------------
  always_comb begin
    sa_flit_o = '0;
    for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
      if (sa_grant_i[v]) begin
        sa_flit_o       = buf_rd_flit[v];
        // Rewrite the VC id to the allocated output VC. The vnet is unchanged
        // by construction -- vc_allocator only offers VCs within the same vnet
        // -- and that is asserted below.
        sa_flit_o.vc_id = vc_to_id(vc_out_vc_q[v]);
      end
    end
  end

  //---------------------------------------------------------------------------
  // Credit return, REGISTERED.
  //
  // These cross the link to the upstream router, so the registered-output rule
  // applies: a combinational credit return would both put the upstream's credit
  // counter on this router's allocation critical path and, more subtly, make
  // the credit visible to an observer in the same cycle the flit is still being
  // read -- so anything sampling after the clock edge sees the *next* cycle's
  // value. It costs one cycle of credit round-trip latency, which is accounted
  // for in the Phase 3 buffer-depth analysis.
  //---------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      credit_valid_o <= 1'b0;
      credit_vc_o    <= '0;
      credit_tail_o  <= 1'b0;
    end else begin
      credit_valid_o <= 1'b0;
      credit_vc_o    <= '0;
      credit_tail_o  <= 1'b0;
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        if (sa_grant_i[v]) begin
          credit_valid_o <= 1'b1;
          credit_vc_o    <= VC_SEL_W'(v);
          credit_tail_o  <= buf_rd_flit[v].tail;
        end
      end
    end
  end

`ifndef SYNTHESIS
  // A head may only arrive at an idle VC. If a_head_only_when_idle fires, the
  // upstream freed an output VC before this VC drained -- see the header note.
  for (genvar v = 0; v < int'(VCS_PER_PORT); v++) begin : gen_vc_asserts
    a_head_only_when_idle : assert property (@(posedge clk) disable iff (!rst_n)
      head_write[v] |-> (vc_state_q[v] == VC_IDLE))
      else $error("input_unit: head flit arrived at VC %0d in state %0d -- the upstream reallocated an output VC before this VC drained", v, vc_state_q[v]);

    a_no_overflow : assert property (@(posedge clk) disable iff (!rst_n)
      buf_wr_valid[v] |-> buf_wr_ready[v])
      else $error("input_unit: VC %0d buffer overflow -- a flit was sent without credit", v);

    a_no_vnet_change : assert property (@(posedge clk) disable iff (!rst_n)
      sa_grant_i[v] |-> (vc_to_vnet(vc_out_vc_q[v]) == buf_rd_flit[v].vnet))
      else $error("input_unit: VC %0d would move a packet from vnet %0d to vnet %0d", v, buf_rd_flit[v].vnet, vc_to_vnet(vc_out_vc_q[v]));

    a_sa_only_when_allocated : assert property (@(posedge clk) disable iff (!rst_n)
      sa_grant_i[v] |-> ((vc_state_q[v] == VC_ALLOC) || (vc_state_q[v] == VC_ACTIVE)))
      else $error("input_unit: VC %0d sent a flit without owning an output VC", v);

    a_body_only_after_head : assert property (@(posedge clk) disable iff (!rst_n)
      (sa_grant_i[v] && !buf_rd_flit[v].head) |-> (vc_state_q[v] == VC_ACTIVE))
      else $error("input_unit: VC %0d sent a body flit before its head", v);
  end
`endif

endmodule : input_unit
