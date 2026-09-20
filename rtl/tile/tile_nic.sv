//=============================================================================
// tile_nic.sv
//
// Network interface for one tile. Packetizes coherence messages into flits and
// reassembles them on the way out, and owns virtual-network assignment,
// destination-tile computation and local-port flow control.
//
// Interfaces: clk, rst_n, tile_id_i; the L1's and the directory's message
// ports on each virtual network; and the router's local port -- a flit stream
// each way with credit return each way.
//
// Three non-obvious things.
//
// 1. THE THREE VIRTUAL NETWORKS ARE STRUCTURALLY SEPARATE HERE, not merged
//    into one queue and tagged. Each has its own packetizer, its own
//    reassembly path and its own credits, and only the single physical flit
//    port is shared -- arbitrated per FLIT, not per packet. This is where the
//    deadlock argument is physically implemented: if a blocked VN0 request
//    could hold the injection path, a VN2 response that would have unblocked
//    it could not get out, and the dependency-ordering argument collapses.
//
// 2. Arbitration is per flit, not per packet, and that is safe because
//    wormhole routing requires a packet's flits to be contiguous on its VIRTUAL
//    CHANNEL, not on the physical port. Two packets on different VCs may
//    interleave on the wire; two packets on the same VC may not, and the VC
//    allocation below never lets that happen.
//
// 3. A VC is released only when the credit for its TAIL comes back, matching
//    the router's own rule (decision D9). Releasing it when the tail is merely
//    sent would let the next packet's head arrive at a downstream VC that is
//    still draining.
//=============================================================================

module tile_nic
  import coh_pkg::*;
#(
  parameter int unsigned TILE_ID = 0
) (
  input  logic                      clk,
  input  logic                      rst_n,

  // ---- L1 side ----
  input  logic                      l1_vn0_valid_i,
  output logic                      l1_vn0_ready_o,
  input  coh_msg_t                  l1_vn0_msg_i,
  input  logic                      l1_vn2_valid_i,
  output logic                      l1_vn2_ready_o,
  input  coh_msg_t                  l1_vn2_msg_i,
  output logic                      l1_vn1_valid_o,
  input  logic                      l1_vn1_ready_i,
  output coh_msg_t                  l1_vn1_msg_o,
  output logic                      l1_vn2_valid_o,
  input  logic                      l1_vn2_ready_i,
  output coh_msg_t                  l1_vn2_msg_o,

  // ---- Directory side ----
  input  logic                      dir_vn1_valid_i,
  output logic                      dir_vn1_ready_o,
  input  coh_msg_t                  dir_vn1_msg_i,
  input  logic                      dir_vn2_valid_i,
  output logic                      dir_vn2_ready_o,
  input  coh_msg_t                  dir_vn2_msg_i,
  output logic                      dir_vn0_valid_o,
  input  logic                      dir_vn0_ready_i,
  output coh_msg_t                  dir_vn0_msg_o,
  output logic                      dir_vn2_valid_o,
  input  logic                      dir_vn2_ready_i,
  output coh_msg_t                  dir_vn2_msg_o,

  // ---- Router local port ----
  output logic                      flit_valid_o,
  output flit_t                     flit_o,
  input  logic                      credit_valid_i,
  input  logic [VC_SEL_W-1:0]       credit_vc_i,
  input  logic                      credit_tail_i,

  input  logic                      flit_valid_i,
  input  flit_t                     flit_i,
  output logic                      credit_valid_o,
  output logic [VC_SEL_W-1:0]       credit_vc_o,
  output logic                      credit_tail_o
);

  //===========================================================================
  // Injection
  //===========================================================================
  // One packetizer per virtual network. VN2 has two feeders (the L1's
  // responses and the directory's), arbitrated into the single VN2 engine.
  coh_msg_t src_msg   [NUM_VNETS];
  logic     src_valid [NUM_VNETS];
  logic     src_ready [NUM_VNETS];

  // VN0 is fed by the L1 only; VN1 by the directory only.
  assign src_valid[0] = l1_vn0_valid_i;
  assign src_msg[0]   = l1_vn0_msg_i;
  assign l1_vn0_ready_o = src_ready[0];

  assign src_valid[1] = dir_vn1_valid_i;
  assign src_msg[1]   = dir_vn1_msg_i;
  assign dir_vn1_ready_o = src_ready[1];

  // VN2 has two feeders on this tile -- the L1's responses and the directory's
  // -- and they are arbitrated ROUND ROBIN, not by priority. Strict priority
  // here starves whichever side loses: with the L1 always winning, a directory
  // that owes a requester its data can be held off indefinitely while that
  // requester's TBE ages out. Both sides carry responses that some other node
  // is blocked on, so neither may be permanently deferred. Bug B10.
  logic [1:0] vn2_req;
  logic [1:0] vn2_gnt;
  logic       vn2_gnt_valid;
  logic       vn2_pick_l1;

  assign vn2_req = {dir_vn2_valid_i, l1_vn2_valid_i};

  rr_arbiter #(.N (2)) u_vn2_arb (
    .clk (clk), .rst_n (rst_n),
    .req_i (vn2_req), .take_i (src_ready[2]),
    .gnt_o (vn2_gnt), .gnt_valid_o (vn2_gnt_valid), .gnt_idx_o ()
  );

  assign vn2_pick_l1  = vn2_gnt[0];
  assign src_valid[2] = vn2_gnt_valid;
  assign src_msg[2]   = vn2_pick_l1 ? l1_vn2_msg_i : dir_vn2_msg_i;
  assign l1_vn2_ready_o  = src_ready[2] && vn2_pick_l1;
  assign dir_vn2_ready_o = src_ready[2] && !vn2_pick_l1;

  // Per-VC credits and occupancy for the downstream (router) input VCs.
  logic [VCS_PER_PORT-1:0] out_credit_send;
  logic [VCS_PER_PORT-1:0] out_credit_ret;
  logic [VCS_PER_PORT-1:0] out_has_credit;
  logic [VCS_PER_PORT-1:0] out_vc_busy_q;

  for (genvar v = 0; v < int'(VCS_PER_PORT); v++) begin : gen_out_credit
    assign out_credit_ret[v] = credit_valid_i && (credit_vc_i == VC_SEL_W'(v));
    credit_counter #(.DEPTH (VC_DEPTH)) u_credit (
      .clk (clk), .rst_n (rst_n),
      .send_i (out_credit_send[v]), .credit_ret_i (out_credit_ret[v]),
      .credits_o (), .has_credit_o (out_has_credit[v])
    );
  end

  // Packetizer state, one per vnet.
  typedef enum logic [1:0] { P_IDLE = 2'd0, P_HEAD = 2'd1, P_BODY = 2'd2 } pk_e;

  pk_e                    pk_q     [NUM_VNETS];
  coh_msg_t               pk_msg_q [NUM_VNETS];
  logic [VC_SEL_W-1:0]    pk_vc_q  [NUM_VNETS];
  logic [1:0]             pk_beat_q[NUM_VNETS];

  // The VC within this vnet that this tile's packets use. It is a function of
  // the tile id and nothing else, and it never changes along the path.
  //
  // Picking the lowest FREE VC here instead -- which is what this did -- lets
  // two messages from the same sender to the same receiver travel on
  // different virtual channels and arrive out of order. The coherence
  // protocol cannot survive that: a directory sends a forward to a cache and
  // then, on processing that cache's Put, a Put-Ack to the same cache. If the
  // Put-Ack overtakes the forward, the cache retires its transaction and the
  // forward lands in state I, where there is no data left to answer with and
  // the requester waits forever. Bug B19.
  logic [VC_SEL_W-1:0] pk_free_vc   [NUM_VNETS];
  logic                pk_has_free  [NUM_VNETS];

  always_comb begin
    for (int unsigned n = 0; n < NUM_VNETS; n++) begin
      pk_free_vc[n]  = vc_index(vnet_e'(n[VNET_W-1:0]),
                                src_vc_id(TILE_ID_W'(TILE_ID)));
      pk_has_free[n] = !out_vc_busy_q[pk_free_vc[n]] &&
                       out_has_credit[pk_free_vc[n]];
    end
  end

  // Which vnets have a flit to offer this cycle.
  logic [NUM_VNETS-1:0] inj_req;
  logic                 inj_gnt_valid;
  logic [VNET_W-1:0]    inj_gnt_idx;

  always_comb begin
    for (int unsigned n = 0; n < NUM_VNETS; n++) begin
      if (pk_q[n] == P_IDLE) begin
        inj_req[n] = src_valid[n] && pk_has_free[n];
      end else begin
        inj_req[n] = out_has_credit[pk_vc_q[n]];
      end
    end
  end

  rr_arbiter #(.N (NUM_VNETS)) u_inj_arb (
    .clk (clk), .rst_n (rst_n),
    .req_i (inj_req), .take_i (1'b1),
    .gnt_o (), .gnt_valid_o (inj_gnt_valid), .gnt_idx_o (inj_gnt_idx)
  );

  logic [VNET_W-1:0]   cur_n;
  logic                cur_start;
  coh_msg_t            cur_msg;
  logic [VC_SEL_W-1:0] cur_vc;
  logic                cur_is_data;
  logic                cur_last;

  assign cur_n     = inj_gnt_idx;
  assign cur_start = (pk_q[cur_n] == P_IDLE);
  assign cur_msg   = cur_start ? src_msg[cur_n] : pk_msg_q[cur_n];
  assign cur_vc    = cur_start ? pk_free_vc[cur_n] : pk_vc_q[cur_n];
  assign cur_is_data = msg_carries_data(cur_msg.msg_type);
  assign cur_last  = cur_start ? !cur_is_data
                               : (pk_beat_q[cur_n] == 2'(FLITS_PER_LINE - 1));

  head_payload_t head_payload;
  always_comb begin
    head_payload           = '0;
    head_payload.msg_type  = cur_msg.msg_type;
    head_payload.addr      = cur_msg.addr;
    head_payload.requester = cur_msg.requester;
    head_payload.ack_count = cur_msg.ack_count;
  end

  always_comb begin
    flit_valid_o = inj_gnt_valid;
    flit_o       = '0;
    flit_o.head  = cur_start;
    flit_o.tail  = cur_last;
    flit_o.vnet  = vnet_e'(cur_n);
    flit_o.vc_id = vc_to_id(cur_vc);
    flit_o.dst_x = tile_x(cur_msg.dst);
    flit_o.dst_y = tile_y(cur_msg.dst);
    flit_o.src_id = TILE_ID_W'(TILE_ID);
    if (cur_start) begin
      flit_o.payload = {{HEAD_PAD_W{1'b0}}, head_payload};
    end else begin
      flit_o.payload = cur_msg.data[pk_beat_q[cur_n] * FLIT_PAYLOAD_W +: FLIT_PAYLOAD_W];
    end
  end

  always_comb begin
    out_credit_send = '0;
    if (inj_gnt_valid) begin
      out_credit_send[cur_vc] = 1'b1;
    end
    for (int unsigned n = 0; n < NUM_VNETS; n++) begin
      src_ready[n] = inj_gnt_valid && (cur_n == VNET_W'(n)) && cur_start;
    end
  end

  //===========================================================================
  // Ejection
  //===========================================================================
  // One reassembly slot per incoming VC. A packet's flits arrive contiguously
  // on their VC, so a slot only ever holds one packet.
  coh_msg_t            rx_msg_q  [VCS_PER_PORT];
  logic                rx_busy_q [VCS_PER_PORT];
  logic                rx_full_q [VCS_PER_PORT];
  logic [1:0]          rx_beat_q [VCS_PER_PORT];

  logic [VC_SEL_W-1:0] in_vc;
  assign in_vc = vc_index(flit_i.vnet, flit_i.vc_id);

  head_payload_t rx_head;
  assign rx_head = head_payload_t'(flit_i.payload[HEAD_PAYLOAD_W-1:0]);

  // Ejection is THREE INDEPENDENT PATHS, one per virtual network. A single
  // selector across all VCs would let a stalled VN0 slot -- a directory
  // blocked head-of-line is an ordinary, expected condition -- starve the VN1
  // and VN2 slots behind it, and VN2 starvation is a deadlock: the response
  // sitting in that slot is what would have unblocked the directory. This is
  // the spec's "keep the three paths structurally separate rather than sharing
  // one queue", and it is where the deadlock argument is physically
  // implemented on the receive side. Bug B11.
  logic     [NUM_VNETS-1:0]               ej_valid;
  logic     [NUM_VNETS-1:0][VC_SEL_W-1:0] ej_vc;
  coh_msg_t [NUM_VNETS-1:0]               ej_msg;
  logic     [NUM_VNETS-1:0]               ej_accept;

  always_comb begin
    for (int unsigned n = 0; n < NUM_VNETS; n++) begin
      ej_valid[n] = 1'b0;
      ej_vc[n]    = '0;
      for (int unsigned k = VCS_PER_VNET; k > 0; k--) begin
        automatic logic [VC_SEL_W-1:0] idx =
            vc_index(vnet_e'(n[VNET_W-1:0]), VC_ID_W'(k - 1));
        if (rx_full_q[idx]) begin
          ej_valid[n] = 1'b1;
          ej_vc[n]    = idx;
        end
      end
      ej_msg[n] = rx_msg_q[ej_vc[n]];
    end
  end

  logic vn2_out_to_dir;
  assign vn2_out_to_dir = vn2_consumer_is_dir(ej_msg[2].msg_type);

  // Valid outputs are computed in a block that reads NO ready input. That
  // separation is not cosmetic: an L1 legitimately derives its vn1_ready_o
  // from vn1_valid_i, so if valid and ready shared one always_comb the tool
  // would see -- correctly -- a combinational cycle through the pair.
  always_comb begin
    dir_vn0_valid_o = ej_valid[0];
    dir_vn0_msg_o   = ej_msg[0];

    l1_vn1_valid_o  = ej_valid[1];
    l1_vn1_msg_o    = ej_msg[1];

    l1_vn2_valid_o  = ej_valid[2] && !vn2_out_to_dir;
    l1_vn2_msg_o    = ej_msg[2];
    dir_vn2_valid_o = ej_valid[2] &&  vn2_out_to_dir;
    dir_vn2_msg_o   = ej_msg[2];
  end

  always_comb begin
    ej_accept[0] = ej_valid[0] && dir_vn0_ready_i;
    ej_accept[1] = ej_valid[1] && l1_vn1_ready_i;
    ej_accept[2] = ej_valid[2] &&
                   (vn2_out_to_dir ? dir_vn2_ready_i : l1_vn2_ready_i);
  end

  // Credit return. Two credits can become due in the same cycle -- a body
  // flit lands while a previously assembled message is taken -- and only one
  // can be sent per cycle, so they are accumulated per VC rather than
  // multiplexed. An earlier version used a single write port with an else-if
  // and silently DROPPED the tail credit whenever a body flit arrived at the
  // same time; the VC was then marked busy forever at both ends and the whole
  // tile wedged once every VC in a vnet had been lost. Bug B12.
  //
  // Body credits and tail credits are tracked separately because they mean
  // different things: a body credit frees a buffer slot, a tail credit frees
  // the VC itself. The tail is deliberately deferred until the assembled
  // message has been handed to the L1 or the directory -- the same reasoning
  // as decision D9, one level further out.
  logic [CREDIT_W-1:0]     pend_body_q [VCS_PER_PORT];
  logic [VCS_PER_PORT-1:0] pend_tail_q;

  logic                    cr_any;
  logic [VC_SEL_W-1:0]     cr_vc;
  logic                    cr_is_tail;

  logic [VCS_PER_PORT-1:0] pend_tail_set;
  logic [VCS_PER_PORT-1:0] pend_tail_clr;
  logic [VCS_PER_PORT-1:0] pend_body_inc;
  logic [VCS_PER_PORT-1:0] pend_body_dec;

  always_comb begin
    cr_any     = 1'b0;
    cr_vc      = '0;
    cr_is_tail = 1'b0;
    // Tails first: they unblock a whole VC, a body credit only a slot.
    for (int unsigned v = VCS_PER_PORT; v > 0; v--) begin
      if (pend_body_q[v - 1] != '0) begin
        cr_any     = 1'b1;
        cr_vc      = VC_SEL_W'(v - 1);
        cr_is_tail = 1'b0;
      end
    end
    for (int unsigned v = VCS_PER_PORT; v > 0; v--) begin
      if (pend_tail_q[v - 1]) begin
        cr_any     = 1'b1;
        cr_vc      = VC_SEL_W'(v - 1);
        cr_is_tail = 1'b1;
      end
    end
  end

  always_comb begin
    pend_tail_set = '0;
    pend_tail_clr = '0;
    pend_body_inc = '0;
    pend_body_dec = '0;
    for (int unsigned n = 0; n < NUM_VNETS; n++) begin
      if (ej_accept[n]) begin
        pend_tail_set[ej_vc[n]] = 1'b1;
      end
    end
    if (flit_valid_i && !flit_i.tail) begin
      pend_body_inc[in_vc] = 1'b1;
    end
    if (cr_any) begin
      if (cr_is_tail) begin
        pend_tail_clr[cr_vc] = 1'b1;
      end else begin
        pend_body_dec[cr_vc] = 1'b1;
      end
    end
  end

  //===========================================================================
  // Sequential
  //===========================================================================
  // A non-tail flit frees a buffer slot immediately; a tail frees the VC, and
  // that is deferred until the assembled message has been taken.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_vc_busy_q  <= '0;
      pend_tail_q    <= '0;
      credit_valid_o <= 1'b0;
      credit_vc_o    <= '0;
      credit_tail_o  <= 1'b0;
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        pend_body_q[v] <= '0;
      end
      for (int unsigned n = 0; n < NUM_VNETS; n++) begin
        pk_q[n]      <= P_IDLE;
        pk_msg_q[n]  <= '0;
        pk_vc_q[n]   <= '0;
        pk_beat_q[n] <= '0;
      end
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        rx_msg_q[v]  <= '0;
        rx_busy_q[v] <= 1'b0;
        rx_full_q[v] <= 1'b0;
        rx_beat_q[v] <= '0;
      end
    end else begin
      credit_valid_o <= 1'b0;
      credit_vc_o    <= '0;
      credit_tail_o  <= 1'b0;

      // ---- Injection ----
      if (inj_gnt_valid) begin
        out_vc_busy_q[cur_vc] <= 1'b1;
        if (cur_start) begin
          pk_msg_q[cur_n] <= src_msg[cur_n];
          pk_vc_q[cur_n]  <= cur_vc;
          pk_beat_q[cur_n] <= '0;
          pk_q[cur_n] <= cur_is_data ? P_BODY : P_IDLE;
        end else begin
          pk_beat_q[cur_n] <= pk_beat_q[cur_n] + 2'd1;
          if (cur_last) begin
            pk_q[cur_n] <= P_IDLE;
          end
        end
      end

      // An output VC is released only when the credit for its tail returns.
      if (credit_valid_i && credit_tail_i) begin
        out_vc_busy_q[credit_vc_i] <= 1'b0;
      end

      // ---- Ejection ----
      if (flit_valid_i) begin
        if (flit_i.head) begin
          rx_msg_q[in_vc].msg_type  <= rx_head.msg_type;
          rx_msg_q[in_vc].addr      <= rx_head.addr;
          rx_msg_q[in_vc].requester <= rx_head.requester;
          rx_msg_q[in_vc].ack_count <= rx_head.ack_count;
          rx_msg_q[in_vc].src       <= flit_i.src_id;
          rx_msg_q[in_vc].dst       <= TILE_ID_W'(TILE_ID);
          rx_beat_q[in_vc]          <= '0;
          rx_busy_q[in_vc]          <= !flit_i.tail;
          rx_full_q[in_vc]          <= flit_i.tail;
        end else begin
          rx_msg_q[in_vc].data[rx_beat_q[in_vc] * FLIT_PAYLOAD_W +: FLIT_PAYLOAD_W]
              <= flit_i.payload;
          rx_beat_q[in_vc] <= rx_beat_q[in_vc] + 2'd1;
          if (flit_i.tail) begin
            rx_busy_q[in_vc] <= 1'b0;
            rx_full_q[in_vc] <= 1'b1;
          end
        end
      end

      for (int unsigned n = 0; n < NUM_VNETS; n++) begin
        if (ej_accept[n]) begin
          rx_full_q[ej_vc[n]] <= 1'b0;
        end
      end

      // Both counters are updated as a SINGLE next-value expression rather
      // than as a set followed by a clear. Writing them as separate
      // conditional statements means the later statement wins when both apply
      // to the same VC in the same cycle, which silently discards whichever
      // update came first -- a tail credit that arrives in the same cycle one
      // is emitted, or a body flit that lands in the same cycle its credit
      // goes out. The correct order is clear-then-set, and expressing it as an
      // expression is the only way to be sure that is what happens.
      pend_tail_q <= (pend_tail_q & ~pend_tail_clr) | pend_tail_set;
      for (int unsigned v = 0; v < VCS_PER_PORT; v++) begin
        pend_body_q[v] <= pend_body_q[v]
                          + (pend_body_inc[v] ? CREDIT_W'(1) : CREDIT_W'(0))
                          - (pend_body_dec[v] ? CREDIT_W'(1) : CREDIT_W'(0));
      end

      credit_valid_o <= cr_any;
      credit_vc_o    <= cr_vc;
      credit_tail_o  <= cr_is_tail;
    end
  end

`ifndef SYNTHESIS
  // A packet may never change virtual network. This is the property the
  // three-vnet dependency argument rests on, checked where the vnet is
  // actually chosen.
  a_vnet_assignment : assert property (@(posedge clk) disable iff (!rst_n)
    (flit_valid_o && flit_o.head)
      |-> (flit_o.vnet == msg_vnet(msg_type_e'(head_payload.msg_type))))
    else $error("tile_nic %0d: launched message type %0d on vnet %0d, which is not its class", TILE_ID, head_payload.msg_type, flit_o.vnet);

  a_vc_in_vnet : assert property (@(posedge clk) disable iff (!rst_n)
    flit_valid_o |-> (vc_to_vnet(cur_vc) == flit_o.vnet))
    else $error("tile_nic %0d: used VC %0d, which is not in vnet %0d", TILE_ID, cur_vc, flit_o.vnet);

  a_no_head_into_busy_slot : assert property (@(posedge clk) disable iff (!rst_n)
    (flit_valid_i && flit_i.head) |-> (!rx_busy_q[in_vc] && !rx_full_q[in_vc]))
    else $error("tile_nic %0d: a head flit arrived on VC %0d while its reassembly slot was still occupied", TILE_ID, in_vc);

  a_body_needs_head : assert property (@(posedge clk) disable iff (!rst_n)
    (flit_valid_i && !flit_i.head) |-> rx_busy_q[in_vc])
    else $error("tile_nic %0d: a body flit arrived on VC %0d with no head", TILE_ID, in_vc);

  // A VC can owe at most VC_DEPTH body credits; more means one was counted
  // twice or never returned.
  for (genvar v = 0; v < int'(VCS_PER_PORT); v++) begin : gen_credit_bound
    a_pending_body_bound : assert property (@(posedge clk) disable iff (!rst_n)
      pend_body_q[v] <= CREDIT_W'(VC_DEPTH))
      else $error("tile_nic %0d: VC %0d owes %0d body credits, more than its depth", TILE_ID, v, pend_body_q[v]);
  end

  a_tail_credit_not_lost : assert property (@(posedge clk) disable iff (!rst_n)
    (|ej_accept) |=> (|pend_tail_q || (credit_valid_o && credit_tail_o)))
    else $error("tile_nic %0d: a message was accepted but no tail credit became pending -- that VC is now busy forever", TILE_ID);
`endif

endmodule : tile_nic
