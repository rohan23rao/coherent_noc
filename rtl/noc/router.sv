//=============================================================================
// router.sv
//
// Five-port input-buffered VC router. Three pipeline stages, all outputs
// registered:
//   BW+RC  buffer write and route compute        (input_unit)
//   VA+SA  VC allocation and switch allocation   (vc_allocator, switch_allocator)
//   ST     crossbar traversal, registered out    (crossbar + output regs)
// Link traversal is the wire to the next router.
//
// Interfaces: per port, an inbound flit with its credit return, and an
// outbound flit with its credit return. Flow control is credit-based; there is
// no ready/valid on the flit path.
//
// The one non-obvious thing: an output VC is marked busy when it is allocated
// and released only when the downstream returns the credit *for the tail
// flit*, not when this router sends the tail. That is one credit round trip of
// extra occupancy per VC, and it is what guarantees a head flit never arrives
// at a downstream VC that is still draining the previous packet. With
// VCS_PER_VNET=2 the sibling VC covers the gap, so the throughput cost is
// small; the correctness it buys is absolute. See docs/decisions.md D9.
//=============================================================================

module router
  import coh_pkg::*;
#(
  parameter int unsigned MY_X = 0,
  parameter int unsigned MY_Y = 0
) (
  input  logic                                        clk,
  input  logic                                        rst_n,

  // Inbound links.
  input  logic  [NUM_PORTS-1:0]                       flit_valid_i,
  input  flit_t [NUM_PORTS-1:0]                       flit_i,
  output logic  [NUM_PORTS-1:0]                       credit_valid_o,
  output logic  [NUM_PORTS-1:0][VC_SEL_W-1:0]         credit_vc_o,
  output logic  [NUM_PORTS-1:0]                       credit_tail_o,

  // Outbound links.
  output logic  [NUM_PORTS-1:0]                       flit_valid_o,
  output flit_t [NUM_PORTS-1:0]                       flit_o,
  input  logic  [NUM_PORTS-1:0]                       credit_valid_i,
  input  logic  [NUM_PORTS-1:0][VC_SEL_W-1:0]         credit_vc_i,
  input  logic  [NUM_PORTS-1:0]                       credit_tail_i
);

  //---------------------------------------------------------------------------
  // Input units
  //---------------------------------------------------------------------------
  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]              va_req;
  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0][PORT_W-1:0]  vc_out_port;
  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]              va_grant;
  logic  [NUM_PORTS-1:0][VC_SEL_W-1:0]                  va_grant_vc;

  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]              sa_req_raw;
  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]              sa_req;
  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0][VC_SEL_W-1:0] vc_out_vc;
  logic  [NUM_PORTS-1:0][VCS_PER_PORT-1:0]              sa_grant;
  flit_t [NUM_PORTS-1:0]                                sa_flit;

  for (genvar p = 0; p < int'(NUM_PORTS); p++) begin : gen_input_unit
    input_unit #(
      .MY_X (MY_X),
      .MY_Y (MY_Y)
    ) u_input_unit (
      .clk            (clk),
      .rst_n          (rst_n),
      .flit_valid_i   (flit_valid_i[p]),
      .flit_i         (flit_i[p]),
      .credit_valid_o (credit_valid_o[p]),
      .credit_vc_o    (credit_vc_o[p]),
      .credit_tail_o  (credit_tail_o[p]),
      .va_req_o       (va_req[p]),
      .vc_out_port_o  (vc_out_port[p]),
      .va_grant_i     (va_grant[p]),
      .va_grant_vc_i  (va_grant_vc[p]),
      .sa_req_o       (sa_req_raw[p]),
      .vc_out_vc_o    (vc_out_vc[p]),
      .sa_grant_i     (sa_grant[p]),
      .sa_flit_o      (sa_flit[p])
    );
  end

  //---------------------------------------------------------------------------
  // Output VC occupancy and downstream credit
  //---------------------------------------------------------------------------
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0] out_vc_busy_q;
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0] va_alloc;
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0] has_credit;
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0] credit_send;
  logic [NUM_PORTS-1:0][VCS_PER_PORT-1:0] credit_ret;

  for (genvar o = 0; o < int'(NUM_PORTS); o++) begin : gen_out_port
    for (genvar v = 0; v < int'(VCS_PER_PORT); v++) begin : gen_out_vc
      assign credit_ret[o][v] = credit_valid_i[o] && (credit_vc_i[o] == VC_SEL_W'(v));

      credit_counter #(
        .DEPTH (VC_DEPTH)
      ) u_credit (
        .clk          (clk),
        .rst_n        (rst_n),
        .send_i       (credit_send[o][v]),
        .credit_ret_i (credit_ret[o][v]),
        .credits_o    (),
        .has_credit_o (has_credit[o][v])
      );
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_vc_busy_q <= '0;
    end else begin
      for (int unsigned o = 0; o < NUM_PORTS; o++) begin
        for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
          if (va_alloc[o][v]) begin
            out_vc_busy_q[o][v] <= 1'b1;
          end else if (credit_valid_i[o] && (credit_vc_i[o] == VC_SEL_W'(v))
                       && credit_tail_i[o]) begin
            out_vc_busy_q[o][v] <= 1'b0;
          end
        end
      end
    end
  end

  //---------------------------------------------------------------------------
  // Allocators. Switch requests are credit-masked before arbitration.
  //---------------------------------------------------------------------------
  always_comb begin
    for (int unsigned p = 0; p < NUM_PORTS; p++) begin
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        sa_req[p][v] = sa_req_raw[p][v]
                       && has_credit[vc_out_port[p][v]][vc_out_vc[p][v]];
      end
    end
  end

  vc_allocator u_vc_alloc (
    .clk            (clk),
    .rst_n          (rst_n),
    .req_i          (va_req),
    .out_port_i     (vc_out_port),
    .out_vc_busy_i  (out_vc_busy_q),
    .grant_o        (va_grant),
    .grant_vc_o     (va_grant_vc),
    .alloc_o        (va_alloc)
  );

  logic [NUM_PORTS-1:0]             xbar_valid;
  logic [NUM_PORTS-1:0][PORT_W-1:0] xbar_sel;

  switch_allocator u_sw_alloc (
    .clk          (clk),
    .rst_n        (rst_n),
    .req_i        (sa_req),
    .out_port_i   (vc_out_port),
    .grant_o      (sa_grant),
    .xbar_valid_o (xbar_valid),
    .xbar_sel_o   (xbar_sel)
  );

  //---------------------------------------------------------------------------
  // Which output VC each departing flit uses, for credit accounting.
  //---------------------------------------------------------------------------
  always_comb begin
    credit_send = '0;
    for (int unsigned o = 0; o < NUM_PORTS; o++) begin
      if (xbar_valid[o]) begin
        for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
          if (sa_grant[xbar_sel[o]][v]) begin
            credit_send[o][vc_out_vc[xbar_sel[o]][v]] = 1'b1;
          end
        end
      end
    end
  end

  //---------------------------------------------------------------------------
  // Crossbar and registered outputs (ST stage)
  //---------------------------------------------------------------------------
  logic  [NUM_PORTS-1:0] xbar_out_valid;
  flit_t [NUM_PORTS-1:0] xbar_out_flit;

  crossbar u_crossbar (
    .sel_valid_i (xbar_valid),
    .sel_port_i  (xbar_sel),
    .in_flit_i   (sa_flit),
    .out_valid_o (xbar_out_valid),
    .out_flit_o  (xbar_out_flit)
  );

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      flit_valid_o <= '0;
      flit_o       <= '0;
    end else begin
      flit_valid_o <= xbar_out_valid;
      flit_o       <= xbar_out_flit;
    end
  end

`ifndef SYNTHESIS
  for (genvar o = 0; o < int'(NUM_PORTS); o++) begin : gen_router_asserts
    a_one_credit_send_per_port : assert property (@(posedge clk) disable iff (!rst_n)
      $onehot0(credit_send[o]))
      else $error("router: output port %0d launched flits on two VCs in one cycle", o);

    a_release_only_busy : assert property (@(posedge clk) disable iff (!rst_n)
      (credit_valid_i[o] && credit_tail_i[o])
        |-> out_vc_busy_q[o][credit_vc_i[o]])
      else $error("router: output port %0d got a tail credit for VC %0d which was not busy", o, credit_vc_i[o]);

    a_send_only_when_busy : assert property (@(posedge clk) disable iff (!rst_n)
      (|credit_send[o]) |-> (|(credit_send[o] & out_vc_busy_q[o])))
      else $error("router: output port %0d launched a flit on an unallocated VC", o);
  end
`endif

endmodule : router
