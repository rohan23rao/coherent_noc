//=============================================================================
// dir_ctrl.sv
//
// One L2/directory bank controller. Home for the quarter of the address space
// whose addr[6:5] selects this bank. It is both the last-level cache and the
// directory, and the two are the same storage: directory metadata lives beside
// the L2 tag.
//
// Interfaces: clk, rst_n; VN0 in (requests), VN2 in (data responses), VN1 out
// (forwards and Put-Acks), VN2 out (data), the memory channel, and a debug bus
// exposing per-way metadata.
//
// Three non-obvious things.
//
// 1. VN2 in is a TRUE SINK. It is buffered and `vn2_ready_o` is high unless
//    that buffer is full, and an assertion says it never fills. This is not a
//    convenience: the whole three-virtual-network argument rests on a response
//    always being accepted in bounded time without the receiver having to send
//    anything first. VN0 by contrast may stall for as long as it likes.
//
// 2. The controller handles ONE request at a time and does not pop the VN0
//    head until that request commits. A request that hits a line in S_D is
//    left at the head and retried. That is blanket head-of-line blocking, not
//    the per-line stall the specification prefers -- see docs/decisions.md D13
//    for what it costs and why it is safe.
//
// 3. Everything the directory is waiting on gets a TBE, including a line in
//    S_D. The TBE is not needed to hold state -- S_D is in the metadata -- but
//    it is what makes the liveness bound a single assertion over one structure
//    rather than a per-line timer.
//=============================================================================

module dir_ctrl
  import coh_pkg::*;
#(
  parameter int unsigned BANK_ID  = 0,
  parameter bit          ENABLE_E = 1'b0
) (
  input  logic                        clk,
  input  logic                        rst_n,

  // ---- VN0 in: requests from L1s. May stall. ----
  input  logic                        vn0_valid_i,
  output logic                        vn0_ready_o,
  input  coh_msg_t                    vn0_msg_i,

  // ---- VN2 in: data responses from L1s. Must never stall. ----
  input  logic                        vn2_valid_i,
  output logic                        vn2_ready_o,
  input  coh_msg_t                    vn2_msg_i,

  // ---- VN1 out: forwards, invalidations, Put-Acks ----
  output logic                        vn1_valid_o,
  input  logic                        vn1_ready_i,
  output coh_msg_t                    vn1_msg_o,

  // ---- VN2 out: data ----
  output logic                        vn2_valid_o,
  input  logic                        vn2_ready_i,
  output coh_msg_t                    vn2_msg_o,

  // ---- Memory ----
  output logic                        mem_req_valid_o,
  input  logic                        mem_req_ready_i,
  output logic [LINE_ADDR_W-1:0]      mem_req_addr_o,
  output logic                        mem_req_we_o,
  output logic [LINE_W-1:0]           mem_req_wdata_o,
  input  logic                        mem_resp_valid_i,
  input  logic [LINE_W-1:0]           mem_resp_rdata_i,

  // ---- Debug ----
  input  logic [L2_IDX_W-1:0]         dbg_set_i,
  input  logic [L2_WAY_W-1:0]         dbg_way_i,
  output dir_meta_t                   dbg_meta_o
);

  localparam int unsigned VN0_Q_DEPTH = 8;
  localparam int unsigned VN2_Q_DEPTH = 8;

  //---------------------------------------------------------------------------
  // Input queues
  //---------------------------------------------------------------------------
  logic             vn0_q_rd_valid, vn0_q_rd_ready;
  logic [COH_MSG_W-1:0] vn0_q_rd_data;
  coh_msg_t         vn0_head;

  fifo #(.WIDTH(COH_MSG_W), .DEPTH(VN0_Q_DEPTH)) u_vn0_q (
    .clk (clk), .rst_n (rst_n),
    .wr_valid_i (vn0_valid_i), .wr_ready_o (vn0_ready_o),
    .wr_data_i  (COH_MSG_W'(vn0_msg_i)),
    .rd_valid_o (vn0_q_rd_valid), .rd_ready_i (vn0_q_rd_ready),
    .rd_data_o  (vn0_q_rd_data),
    .count_o (), .empty_o (), .full_o ()
  );
  assign vn0_head = coh_msg_t'(vn0_q_rd_data);

  logic             vn2_q_rd_valid, vn2_q_rd_ready;
  logic [COH_MSG_W-1:0] vn2_q_rd_data;
  coh_msg_t         vn2_head;
  logic             vn2_q_full;

  fifo #(.WIDTH(COH_MSG_W), .DEPTH(VN2_Q_DEPTH)) u_vn2_q (
    .clk (clk), .rst_n (rst_n),
    .wr_valid_i (vn2_valid_i), .wr_ready_o (vn2_ready_o),
    .wr_data_i  (COH_MSG_W'(vn2_msg_i)),
    .rd_valid_o (vn2_q_rd_valid), .rd_ready_i (vn2_q_rd_ready),
    .rd_data_o  (vn2_q_rd_data),
    .count_o (), .empty_o (), .full_o (vn2_q_full)
  );
  assign vn2_head = coh_msg_t'(vn2_q_rd_data);

  //---------------------------------------------------------------------------
  // Storage
  //---------------------------------------------------------------------------
  logic                        l2_lookup_en;
  logic [L2_IDX_W-1:0]         l2_lookup_set;
  logic                        l2_wr_meta_en;
  logic                        l2_wr_data_en;
  logic [L2_IDX_W-1:0]         l2_wr_set;
  logic [L2_WAY_W-1:0]         l2_wr_way;
  dir_meta_t                   l2_wr_meta;
  logic [LINE_W-1:0]           l2_wr_data;
  dir_meta_t [L2_WAYS-1:0]     l2_meta;
  logic [LINE_W-1:0]           l2_data [L2_WAYS];

  l2_bank u_l2 (
    .clk          (clk),
    .rst_n        (rst_n),
    .lookup_en_i  (l2_lookup_en),
    .lookup_set_i (l2_lookup_set),
    .wr_meta_en_i (l2_wr_meta_en),
    .wr_set_i     (l2_wr_set),
    .wr_way_i     (l2_wr_way),
    .wr_meta_i    (l2_wr_meta),
    .wr_data_en_i (l2_wr_data_en),
    .wr_data_i    (l2_wr_data),
    .meta_o       (l2_meta),
    .data_o       (l2_data),
    .dbg_set_i    (dbg_set_i),
    .dbg_way_i    (dbg_way_i),
    .dbg_meta_o   (dbg_meta_o)
  );

  //---------------------------------------------------------------------------
  // TBE file
  //---------------------------------------------------------------------------
  logic                        tbe_alloc_valid, tbe_alloc_ready;
  tbe_state_e                  tbe_alloc_state;
  logic [LINE_ADDR_W-1:0]      tbe_alloc_addr;
  logic [L2_WAY_W-1:0]         tbe_alloc_way;
  logic [TILE_ID_W-1:0]        tbe_alloc_requester;
  logic                        tbe_lookup_hit;
  logic [TBE_IDX_W-1:0]        tbe_lookup_idx;
  logic                        tbe_free_valid;
  logic [TBE_IDX_W-1:0]        tbe_free_idx;

  //---------------------------------------------------------------------------
  // Controller state
  //---------------------------------------------------------------------------
  typedef enum logic [2:0] {
    D_IDLE     = 3'd0,
    D_LOOK     = 3'd1,
    D_MEM_REQ  = 3'd2,
    D_MEM_WAIT = 3'd3,
    D_EXEC     = 3'd4,
    D_WRDATA   = 3'd5,
    D_SEND     = 3'd6
  } dir_fsm_e;

  dir_fsm_e                 fsm_q;
  coh_msg_t                 cur_q;
  logic                     cur_from_vn2_q;
  logic [L2_IDX_W-1:0]      cur_set_q;
  logic [L2_WAY_W-1:0]      cur_way_q;
  dir_meta_t                old_meta_q;
  logic [LINE_W-1:0]        cur_line_q;
  logic [NUM_TILES-1:0]     inv_pend_q;
  logic                     send_data_q;
  logic                     send_data_e_q;
  logic                     send_put_ack_q;
  logic                     send_fwd_gets_q;
  logic                     send_fwd_getm_q;
  logic [ACK_FIELD_W-1:0]   ack_count_q;
  dir_meta_t                new_meta_q;

  //---------------------------------------------------------------------------
  // Selection and lookup
  //---------------------------------------------------------------------------
  logic     take_vn2;
  logic     take_vn0;
  coh_msg_t sel_msg;

  // VN2 has absolute priority: it is the sink, and it is also what unblocks a
  // line sitting in S_D that VN0 is stalled behind.
  assign take_vn2 = (fsm_q == D_IDLE) && vn2_q_rd_valid;
  assign take_vn0 = (fsm_q == D_IDLE) && !vn2_q_rd_valid && vn0_q_rd_valid;
  assign sel_msg  = take_vn2 ? vn2_head : vn0_head;

  assign l2_lookup_en  = (take_vn2 || take_vn0) && !l2_wr_data_en;
  assign l2_lookup_set = line_l2_index(sel_msg.addr);

  //---------------------------------------------------------------------------
  // Way selection over the looked-up set
  //---------------------------------------------------------------------------
  logic [L2_WAYS-1:0]  hit_way_mask;
  logic                hit;
  logic [L2_WAY_W-1:0] hit_way;
  logic [L2_WAYS-1:0]  free_way_mask;
  logic                have_free_way;
  logic [L2_WAY_W-1:0] free_way;

  always_comb begin
    for (int unsigned w = 0; w < L2_WAYS; w++) begin
      hit_way_mask[w]  = l2_meta[w].valid &&
                         (l2_meta[w].tag == line_l2_tag(cur_q.addr));
      free_way_mask[w] = !l2_meta[w].valid;
    end
  end

  assign hit           = |hit_way_mask;
  assign have_free_way = |free_way_mask;

  always_comb begin
    hit_way  = '0;
    free_way = '0;
    for (int unsigned w = L2_WAYS; w > 0; w--) begin
      if (hit_way_mask[w - 1])  hit_way  = L2_WAY_W'(w - 1);
      if (free_way_mask[w - 1]) free_way = L2_WAY_W'(w - 1);
    end
  end

  //---------------------------------------------------------------------------
  // Event classification
  //---------------------------------------------------------------------------
  dir_event_e          dir_event;
  logic [NUM_TILES-1:0] req_bit;
  logic                is_owner;
  logic                is_last_sharer;

  assign req_bit        = NUM_TILES'(1) << cur_q.src;
  assign is_owner       = ((old_meta_q.dir_state == DIR_E) ||
                           (old_meta_q.dir_state == DIR_M)) &&
                          (old_meta_q.owner == cur_q.src);
  assign is_last_sharer = ((old_meta_q.sharers & ~req_bit) == '0);

  always_comb begin
    dir_event = DEV_GETS;
    unique case (cur_q.msg_type)
      MSG_GETS:    dir_event = DEV_GETS;
      MSG_GETM:    dir_event = DEV_GETM;
      MSG_PUTS:    dir_event = is_last_sharer ? DEV_PUTS_LAST : DEV_PUTS_NOT_LAST;
      MSG_PUTM:    dir_event = is_owner ? DEV_PUTM_OWNER : DEV_PUTM_NON_OWNER;
      MSG_PUTE:    dir_event = is_owner ? DEV_PUTE_OWNER : DEV_PUTE_NON_OWNER;
      MSG_WB_DATA: dir_event = DEV_DATA;
      default: begin
        dir_event = DEV_GETS;
        // Only meaningful once a message has actually been latched.
        if (fsm_q == D_EXEC) begin
          $error("dir_ctrl: bank %0d received message type %0d it cannot classify", BANK_ID, cur_q.msg_type);
        end
      end
    endcase
  end

  dir_action_t act;
  dir_state_e  next_dir;

  dir_coh_fsm u_fsm (
    .enable_e_i   (ENABLE_E),
    .state_i      (old_meta_q.dir_state),
    .event_i      (dir_event),
    .next_state_o (next_dir),
    .action_o     (act)
  );

  //---------------------------------------------------------------------------
  // Metadata update
  //---------------------------------------------------------------------------
  dir_meta_t new_meta;
  logic [NUM_TILES-1:0] owner_bit;
  assign owner_bit = NUM_TILES'(1) << old_meta_q.owner;

  always_comb begin
    new_meta = old_meta_q;
    new_meta.dir_state = next_dir;
    if (act.add_sharer)        new_meta.sharers = old_meta_q.sharers | req_bit;
    if (act.remove_sharer)     new_meta.sharers = old_meta_q.sharers & ~req_bit;
    if (act.clear_sharers)     new_meta.sharers = '0;
    if (act.sharers_owner_req) new_meta.sharers = owner_bit | req_bit;
    if (act.set_owner_req)     new_meta.owner   = cur_q.src;
    if (act.clear_owner)       new_meta.owner   = '0;
    if (act.copy_data_l2)      new_meta.data_valid = 1'b1;
    // An owner holding the line dirty makes the L2 copy stale.
    if (act.send_data_e || (act.send_data && (next_dir == DIR_M))) begin
      new_meta.data_valid = 1'b0;
    end
  end

  // Sharers to invalidate on an S + GetM: everyone except the requester.
  logic [NUM_TILES-1:0] inv_targets;
  assign inv_targets = act.send_inv_others ? (old_meta_q.sharers & ~req_bit) : '0;

  logic [ACK_FIELD_W-1:0] ack_count;
  always_comb begin
    ack_count = '0;
    for (int unsigned t = 0; t < NUM_TILES; t++) begin
      if (inv_targets[t]) begin
        ack_count = ack_count + ACK_FIELD_W'(1);
      end
    end
  end

  //---------------------------------------------------------------------------
  // Outgoing messages
  //---------------------------------------------------------------------------
  logic [TILE_ID_W-1:0] inv_target_idx;
  always_comb begin
    inv_target_idx = '0;
    for (int unsigned t = NUM_TILES; t > 0; t--) begin
      if (inv_pend_q[t - 1]) begin
        inv_target_idx = TILE_ID_W'(t - 1);
      end
    end
  end

  logic send_vn2_now;
  logic send_vn1_now;

  assign send_vn2_now = (fsm_q == D_SEND) && (send_data_q || send_data_e_q);
  assign send_vn1_now = (fsm_q == D_SEND) && !send_vn2_now &&
                        (send_put_ack_q || send_fwd_gets_q || send_fwd_getm_q ||
                         (|inv_pend_q));

  always_comb begin
    vn2_valid_o = send_vn2_now;
    vn2_msg_o   = '0;
    vn2_msg_o.addr      = cur_q.addr;
    vn2_msg_o.src       = TILE_ID_W'(BANK_ID);
    vn2_msg_o.dst       = cur_q.src;
    vn2_msg_o.requester = cur_q.src;
    vn2_msg_o.data      = cur_line_q;
    vn2_msg_o.ack_count = ack_count_q;
    vn2_msg_o.msg_type  = send_data_e_q ? MSG_DATA_E : MSG_DATA_DIR;
  end

  always_comb begin
    vn1_valid_o = send_vn1_now;
    vn1_msg_o   = '0;
    vn1_msg_o.addr      = cur_q.addr;
    vn1_msg_o.src       = TILE_ID_W'(BANK_ID);
    vn1_msg_o.requester = cur_q.src;
    vn1_msg_o.data      = '0;
    vn1_msg_o.ack_count = '0;
    if (send_put_ack_q) begin
      vn1_msg_o.msg_type = MSG_PUT_ACK;
      vn1_msg_o.dst      = cur_q.src;
    end else if (send_fwd_gets_q) begin
      vn1_msg_o.msg_type = MSG_FWD_GETS;
      vn1_msg_o.dst      = old_meta_q.owner;
    end else if (send_fwd_getm_q) begin
      vn1_msg_o.msg_type = MSG_FWD_GETM;
      vn1_msg_o.dst      = old_meta_q.owner;
    end else begin
      vn1_msg_o.msg_type = MSG_INV;
      vn1_msg_o.dst      = inv_target_idx;
    end
  end

  //---------------------------------------------------------------------------
  // Memory
  //---------------------------------------------------------------------------
  assign mem_req_valid_o = (fsm_q == D_MEM_REQ);
  assign mem_req_addr_o  = cur_q.addr;
  assign mem_req_we_o    = 1'b0;
  assign mem_req_wdata_o = '0;

  //---------------------------------------------------------------------------
  // Array write ports
  //---------------------------------------------------------------------------
  logic install_now;
  assign install_now = (fsm_q == D_MEM_WAIT) && mem_resp_valid_i;

  always_comb begin
    l2_wr_meta_en = 1'b0;
    l2_wr_data_en = 1'b0;
    l2_wr_set     = cur_set_q;
    l2_wr_way     = cur_way_q;
    l2_wr_meta    = new_meta_q;
    l2_wr_data    = cur_q.data;

    if (install_now) begin
      l2_wr_meta_en = 1'b1;
      l2_wr_data_en = 1'b1;
      l2_wr_data    = mem_resp_rdata_i;
      l2_wr_meta    = '0;
      l2_wr_meta.valid      = 1'b1;
      l2_wr_meta.tag        = line_l2_tag(cur_q.addr);
      l2_wr_meta.dir_state  = DIR_I;
      l2_wr_meta.sharers    = '0;
      l2_wr_meta.owner      = '0;
      l2_wr_meta.data_valid = 1'b1;
    end else if (fsm_q == D_WRDATA) begin
      // One cycle after D_EXEC, so the registered copy is the right one here.
      l2_wr_meta_en = 1'b1;
      l2_wr_data_en = 1'b1;
      l2_wr_meta    = new_meta_q;
      l2_wr_data    = cur_q.data;
    end else if ((fsm_q == D_EXEC) && !act.stall && !act.illegal && !act.copy_data_l2) begin
      // The COMBINATIONAL new_meta, not the register: new_meta_q is loaded at
      // the end of this same cycle and still holds the previous transaction's
      // value. Using it here silently lagged every metadata update by one
      // transaction -- bug B7.
      l2_wr_meta_en = 1'b1;
      l2_wr_meta    = new_meta;
    end
  end

  //---------------------------------------------------------------------------
  // Queue pop
  //---------------------------------------------------------------------------
  logic commit;
  assign commit = (fsm_q == D_SEND) && !send_vn2_now && !send_vn1_now;

  assign vn2_q_rd_ready = commit && cur_from_vn2_q;
  assign vn0_q_rd_ready = commit && !cur_from_vn2_q;

  //---------------------------------------------------------------------------
  // TBE wiring
  //---------------------------------------------------------------------------
  assign tbe_alloc_valid     = (fsm_q == D_EXEC) && !act.stall && !act.illegal &&
                               (next_dir == DIR_S_D) && (old_meta_q.dir_state != DIR_S_D);
  assign tbe_alloc_state     = TBE_WB_PEND;
  assign tbe_alloc_addr      = cur_q.addr;
  assign tbe_alloc_way       = cur_way_q;
  assign tbe_alloc_requester = cur_q.src;
  assign tbe_free_valid      = (fsm_q == D_EXEC) && !act.stall && !act.illegal &&
                               (old_meta_q.dir_state == DIR_S_D) && act.copy_data_l2 &&
                               tbe_lookup_hit;
  assign tbe_free_idx        = tbe_lookup_idx;

  tbe_file u_tbe (
    .clk               (clk),
    .rst_n             (rst_n),
    .alloc_valid_i     (tbe_alloc_valid),
    .alloc_ready_o     (tbe_alloc_ready),
    .alloc_state_i     (tbe_alloc_state),
    .alloc_addr_i      (tbe_alloc_addr),
    .alloc_way_i       (tbe_alloc_way),
    .alloc_requester_i (tbe_alloc_requester),
    .alloc_ack_cnt_i   ('0),
    .alloc_idx_o       (),
    .lookup_addr_i     (cur_q.addr),
    .lookup_hit_o      (tbe_lookup_hit),
    .lookup_idx_o      (tbe_lookup_idx),
    .ack_dec_valid_i   (1'b0),
    .ack_dec_idx_i     ('0),
    .free_valid_i      (tbe_free_valid),
    .free_idx_i        (tbe_free_idx),
    .entries_o         ()
  );

  //---------------------------------------------------------------------------
  // Sequential
  //---------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      fsm_q           <= D_IDLE;
      cur_q           <= '0;
      cur_from_vn2_q  <= 1'b0;
      cur_set_q       <= '0;
      cur_way_q       <= '0;
      old_meta_q      <= '0;
      cur_line_q      <= '0;
      inv_pend_q      <= '0;
      send_data_q     <= 1'b0;
      send_data_e_q   <= 1'b0;
      send_put_ack_q  <= 1'b0;
      send_fwd_gets_q <= 1'b0;
      send_fwd_getm_q <= 1'b0;
      ack_count_q     <= '0;
      new_meta_q      <= '0;
    end else begin
      unique case (fsm_q)
        D_IDLE: begin
          if (take_vn2 || take_vn0) begin
            cur_q          <= sel_msg;
            cur_from_vn2_q <= take_vn2;
            cur_set_q      <= line_l2_index(sel_msg.addr);
            fsm_q          <= D_LOOK;
          end
        end

        D_LOOK: begin
          cur_line_q <= l2_data[hit ? hit_way : free_way];
          if (hit) begin
            cur_way_q  <= hit_way;
            old_meta_q <= l2_meta[hit_way];
            fsm_q      <= D_EXEC;
          end else begin
            cur_way_q <= free_way;
            fsm_q     <= D_MEM_REQ;
          end
        end

        D_MEM_REQ: begin
          if (mem_req_ready_i) begin
            fsm_q <= D_MEM_WAIT;
          end
        end

        D_MEM_WAIT: begin
          if (mem_resp_valid_i) begin
            // The line is installed this cycle; retry the request against it.
            fsm_q <= D_IDLE;
          end
        end

        D_EXEC: begin
          if (act.stall) begin
            // Leave the message at the head of VN0 and retry.
            fsm_q <= D_IDLE;
          end else begin
            new_meta_q      <= new_meta;
            cur_line_q      <= act.copy_data_l2 ? cur_q.data : cur_line_q;
            inv_pend_q      <= inv_targets;
            send_data_q     <= act.send_data;
            send_data_e_q   <= act.send_data_e;
            send_put_ack_q  <= act.send_put_ack;
            send_fwd_gets_q <= act.send_fwd_gets;
            send_fwd_getm_q <= act.send_fwd_getm;
            ack_count_q     <= ack_count;
            fsm_q           <= act.copy_data_l2 ? D_WRDATA : D_SEND;
          end
        end

        D_WRDATA: begin
          fsm_q <= D_SEND;
        end

        D_SEND: begin
          if (send_vn2_now && vn2_ready_i) begin
            send_data_q   <= 1'b0;
            send_data_e_q <= 1'b0;
          end else if (send_vn1_now && vn1_ready_i) begin
            if (send_put_ack_q) begin
              send_put_ack_q <= 1'b0;
            end else if (send_fwd_gets_q) begin
              send_fwd_gets_q <= 1'b0;
            end else if (send_fwd_getm_q) begin
              send_fwd_getm_q <= 1'b0;
            end else begin
              inv_pend_q[inv_target_idx] <= 1'b0;
            end
          end else if (commit) begin
            fsm_q <= D_IDLE;
          end
        end

        default: begin
          fsm_q <= D_IDLE;
          $error("dir_ctrl: bank %0d entered illegal state %0d", BANK_ID, fsm_q);
        end
      endcase
    end
  end

`ifndef SYNTHESIS
  // The sink requirement, stated as an assertion rather than an assumption.
  a_vn2_never_backs_up : assert property (@(posedge clk) disable iff (!rst_n)
    !vn2_q_full)
    else $error("dir_ctrl: bank %0d VN2 input queue filled -- the response network is no longer a sink", BANK_ID);

  a_no_illegal_event : assert property (@(posedge clk) disable iff (!rst_n)
    (fsm_q == D_EXEC) |-> !act.illegal)
    else $error("dir_ctrl: bank %0d took event %0d in state %0d, which the table marks impossible", BANK_ID, dir_event, old_meta_q.dir_state);

  // Phase 6 has no capacity eviction; Phase 9 adds back-invalidation. Until
  // then a set running out of ways is a testbench footprint error, and the
  // assertion says so rather than letting the directory quietly misbehave.
  a_free_way_available : assert property (@(posedge clk) disable iff (!rst_n)
    ((fsm_q == D_LOOK) && !hit) |-> have_free_way)
    else $error("dir_ctrl: bank %0d set %0d is full and back-invalidation does not exist yet (Phase 9)", BANK_ID, cur_set_q);

  a_tbe_alloc_succeeds : assert property (@(posedge clk) disable iff (!rst_n)
    tbe_alloc_valid |-> tbe_alloc_ready)
    else $error("dir_ctrl: bank %0d ran out of TBEs", BANK_ID);
`endif

endmodule : dir_ctrl
