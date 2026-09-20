//=============================================================================
// l1_cache.sv
//
// Private L1 data cache with MESI coherence. 64 sets x 2 ways x 32 B, PIPT,
// write-back / write-allocate, non-blocking with an MSHR file. Every protocol
// transition comes from l1_coh_fsm; this module is the controller that feeds
// it events and carries out its actions.
//
// Interfaces: clk, rst_n, tile_id_i; the core request/response channels; VN0
// out (requests), VN1 in (forwards, invalidations, Put-Acks), VN2 in and out
// (responses); and a debug bus exposing per-way state and tag.
//
// Priority between the four classes of work, highest first, exactly as the
// specification requires:
//   1. VN2 responses      -- must ALWAYS be sinkable, so they can never be
//                            refused and take the MSHR update port outright
//   2. retirement         -- an MSHR whose transaction has completed
//   3. VN1 forwards       -- may stall on a transient state
//   4. new core requests  -- lowest
// Giving coherence traffic priority over new core requests is what makes the
// deadlock argument hold: a core that keeps issuing can never starve the
// responses that let other cores make progress. See docs/deadlock.md.
//
// Three non-obvious things.
//
// 1. An eviction is its OWN MSHR transaction, not a field inside the
//    requesting line's entry. A victim in M/E/S has to run MI_A / EI_A / SI_A
//    until its Put-Ack, and while it does it can still receive Fwd-GetS and
//    Fwd-GetM for its own line -- so it needs its own CAM-matchable address.
//    See docs/decisions.md D14.
//
// 2. A core request that needs an eviction does NOT wait for the Put-Ack. It
//    allocates the eviction MSHR, the victim is invalidated locally, and the
//    request replays and allocates the now-free way. The Put-Ack is protocol
//    bookkeeping on the old line and has nothing to say about the new one.
//
// 3. ack_cnt is signed and lives in the MSHR. IM_AD + Inv-Ack decrements it
//    below zero when acks overtake the data; Data[ack=N] credits it back up.
//    Completion is `ack_cnt == 0 after the data has arrived`, never `ack_cnt
//    reached zero`.
//
// 4. THE TAG ARRAY IS THE SINGLE SOURCE OF TRUTH for the coherence state of
//    any line that is resident or becoming resident, transient states
//    included. A miss writes the tag and the transient state at allocation, so
//    a forward for that line finds it; an upgrade (S + Store, a tag HIT that
//    still needs a GetM) simply moves the resident line to SM_AD in place. The
//    MSHR holds the transaction's bookkeeping -- fill data, ack count, the
//    core's tag -- and the FINAL state to install at retirement, not a second
//    copy of the current one. Only an EVICTION keeps its state in the MSHR,
//    because the array entry it came from has already been given up.
//
//    The array stays transient until retirement, at which point the data and
//    the stable state are written together. That is what makes a load arriving
//    between "the response landed" and "the line is installed" stall rather
//    than read a line whose state says M and whose data is stale.
//=============================================================================

module l1_cache
  import coh_pkg::*;
(
  input  logic                        clk,
  input  logic                        rst_n,
  input  logic [TILE_ID_W-1:0]        tile_id_i,

  input  logic                        core_req_valid_i,
  output logic                        core_req_ready_o,
  input  core_op_e                    core_op_i,
  input  logic [ADDR_W-1:0]           core_addr_i,
  input  logic [WORD_W-1:0]           core_wdata_i,
  input  logic [BE_W-1:0]             core_be_i,
  input  logic [CORE_TAG_W-1:0]       core_tag_i,

  output logic                        core_resp_valid_o,
  output logic [CORE_TAG_W-1:0]       core_resp_tag_o,
  output logic [WORD_W-1:0]           core_resp_rdata_o,

  // VN0 out: requests to the home directory.
  output logic                        vn0_valid_o,
  input  logic                        vn0_ready_i,
  output coh_msg_t                    vn0_msg_o,

  // VN1 in: forwards, invalidations, Put-Acks. May stall.
  input  logic                        vn1_valid_i,
  output logic                        vn1_ready_o,
  input  coh_msg_t                    vn1_msg_i,

  // VN2 in: responses. MUST NEVER STALL.
  input  logic                        vn2_valid_i,
  output logic                        vn2_ready_o,
  input  coh_msg_t                    vn2_msg_i,

  // VN2 out: Inv-Acks, data to a requester, writeback data to a directory.
  output logic                        vn2_valid_o,
  input  logic                        vn2_ready_i,
  output coh_msg_t                    vn2_msg_o,

  // Debug bus for the coherence checker. Every way of one set is exposed at
  // once, because SWMR is a property of a LINE across tiles and a line may sit
  // in a different way in each of them.
  input  logic [L1_IDX_W-1:0]         dbg_set_i,
  output logic [L1_WAYS-1:0][3:0]     dbg_state_o,
  output logic [L1_WAYS-1:0][L1_TAG_W-1:0] dbg_tag_o,
  output logic [MSHR_ENTRIES-1:0]     dbg_mshr_valid_o
);

  localparam int unsigned VN2_OUT_DEPTH = 8;
  localparam int unsigned VN0_OUT_DEPTH = 8;

  //---------------------------------------------------------------------------
  // Arrays: tag and state in flops (D10), data in SRAM.
  //---------------------------------------------------------------------------
  l1_state_e           state_q [L1_SETS][L1_WAYS];
  logic [L1_TAG_W-1:0] tag_q   [L1_SETS][L1_WAYS];
  logic                plru_q  [L1_SETS];

  logic [L1_IDX_W-1:0] data_addr;
  logic                data_en   [L1_WAYS];
  logic                data_we   [L1_WAYS];
  logic [LINE_W-1:0]   data_wdata;
  logic [LINE_W-1:0]   data_rdata[L1_WAYS];

  for (genvar w = 0; w < int'(L1_WAYS); w++) begin : gen_way
    sram_1rw #(.WIDTH(LINE_W), .DEPTH(L1_SETS)) u_data (
      .clk (clk), .en_i (data_en[w]), .we_i (data_we[w]),
      .addr_i (data_addr), .wdata_i (data_wdata), .rdata_o (data_rdata[w])
    );
  end

  //---------------------------------------------------------------------------
  // Output queues
  //---------------------------------------------------------------------------
  logic                 vn0_q_wr_valid, vn0_q_wr_ready;
  coh_msg_t             vn0_q_wr_msg;
  logic [COH_MSG_W-1:0] vn0_q_rd_data;

  fifo #(.WIDTH(COH_MSG_W), .DEPTH(VN0_OUT_DEPTH)) u_vn0_out (
    .clk (clk), .rst_n (rst_n),
    .wr_valid_i (vn0_q_wr_valid), .wr_ready_o (vn0_q_wr_ready),
    .wr_data_i (COH_MSG_W'(vn0_q_wr_msg)),
    .rd_valid_o (vn0_valid_o), .rd_ready_i (vn0_ready_i),
    .rd_data_o (vn0_q_rd_data),
    .count_o (), .empty_o (), .full_o ()
  );
  assign vn0_msg_o = coh_msg_t'(vn0_q_rd_data);

  logic                 vn2_q_wr_valid, vn2_q_wr_ready;
  coh_msg_t             vn2_q_wr_msg;
  logic [COH_MSG_W-1:0] vn2_q_rd_data;

  fifo #(.WIDTH(COH_MSG_W), .DEPTH(VN2_OUT_DEPTH)) u_vn2_out (
    .clk (clk), .rst_n (rst_n),
    .wr_valid_i (vn2_q_wr_valid), .wr_ready_o (vn2_q_wr_ready),
    .wr_data_i (COH_MSG_W'(vn2_q_wr_msg)),
    .rd_valid_o (vn2_valid_o), .rd_ready_i (vn2_ready_i),
    .rd_data_o (vn2_q_rd_data),
    .count_o (), .empty_o (), .full_o ()
  );
  assign vn2_msg_o = coh_msg_t'(vn2_q_rd_data);

  //---------------------------------------------------------------------------
  // MSHR file
  //---------------------------------------------------------------------------
  logic                       mshr_alloc_valid, mshr_alloc_ready;
  logic [LINE_ADDR_W-1:0]     mshr_alloc_addr;
  l1_state_e                  mshr_alloc_state;
  core_op_e                   mshr_alloc_op;
  logic [WORD_W-1:0]          mshr_alloc_wdata;
  logic [BE_W-1:0]            mshr_alloc_be;
  logic [WORD_SEL_W-1:0]      mshr_alloc_word;
  logic [CORE_TAG_W-1:0]      mshr_alloc_tag;
  logic [L1_WAY_W-1:0]        mshr_alloc_way;
  logic [LINE_W-1:0]          mshr_alloc_data;
  logic                       mshr_alloc_is_evict;

  logic [LINE_ADDR_W-1:0]     mshr_lookup_addr;
  logic                       mshr_lookup_hit;

  logic                       mshr_upd_valid;
  logic [MSHR_IDX_W-1:0]      mshr_upd_idx;
  logic                       mshr_upd_set_state;
  l1_state_e                  mshr_upd_state;
  logic                       mshr_upd_set_data;
  logic [LINE_W-1:0]          mshr_upd_data;
  logic                       mshr_upd_ack_dec;
  logic                       mshr_upd_ack_add;
  logic signed [ACK_CNT_W-1:0] mshr_upd_ack_val;
  logic                       mshr_upd_done;

  logic                       mshr_free_valid;
  logic [MSHR_IDX_W-1:0]      mshr_free_idx;

  mshr_e [MSHR_ENTRIES-1:0]   mshr;

  mshr_file u_mshr (
    .clk (clk), .rst_n (rst_n),
    .alloc_valid_i (mshr_alloc_valid), .alloc_ready_o (mshr_alloc_ready),
    .alloc_addr_i (mshr_alloc_addr), .alloc_state_i (mshr_alloc_state),
    .alloc_op_i (mshr_alloc_op), .alloc_wdata_i (mshr_alloc_wdata),
    .alloc_be_i (mshr_alloc_be), .alloc_word_sel_i (mshr_alloc_word),
    .alloc_tag_i (mshr_alloc_tag), .alloc_way_i (mshr_alloc_way),
    .alloc_needs_wb_i (1'b0), .alloc_is_evict_i (mshr_alloc_is_evict),
    .alloc_wb_data_i (mshr_alloc_data), .alloc_wb_addr_i (mshr_alloc_addr),
    .alloc_idx_o (),
    .lookup_addr_i (mshr_lookup_addr), .lookup_hit_o (mshr_lookup_hit),
    .lookup_idx_o (),
    .upd_valid_i (mshr_upd_valid), .upd_idx_i (mshr_upd_idx),
    .upd_set_state_i (mshr_upd_set_state), .upd_state_i (mshr_upd_state),
    .upd_set_data_i (mshr_upd_set_data), .upd_data_i (mshr_upd_data),
    .upd_ack_dec_i (mshr_upd_ack_dec), .upd_ack_add_i (mshr_upd_ack_add),
    .upd_ack_val_i (mshr_upd_ack_val), .upd_done_i (mshr_upd_done),
    .free_valid_i (mshr_free_valid), .free_idx_i (mshr_free_idx),
    .entries_o (mshr)
  );

  //---------------------------------------------------------------------------
  // Home bank for a line: addr[6:5], as a tile id.
  //---------------------------------------------------------------------------
  function automatic logic [TILE_ID_W-1:0] home_of(input logic [LINE_ADDR_W-1:0] a);
    return TILE_ID_W'(line_home_bank(a));
  endfunction

  //---------------------------------------------------------------------------
  // VN2 in: the sink. Always accepted, and it owns the MSHR update port.
  //---------------------------------------------------------------------------
  logic                  vn2_hit;
  logic [MSHR_IDX_W-1:0] vn2_idx;
  l1_event_e             vn2_event;
  l1_state_e             vn2_next;
  l1_action_t            vn2_act;
  logic                  vn2_take;

  assign vn2_take = vn2_valid_i;
  assign vn2_ready_o = 1'b1;   // the sink requirement, structurally

  always_comb begin
    vn2_event = EV_INV_ACK;
    unique case (vn2_msg_i.msg_type)
      MSG_DATA_E:     vn2_event = EV_DATA_E_DIR;
      MSG_DATA_DIR:   vn2_event = (vn2_msg_i.ack_count == '0) ? EV_DATA_DIR_A0
                                                              : EV_DATA_DIR_AGT0;
      MSG_DATA_OWNER: vn2_event = EV_DATA_OWNER;
      MSG_INV_ACK:    vn2_event = EV_INV_ACK;
      default: begin
        vn2_event = EV_INV_ACK;
        // Guarded on valid: the bus is all zeros while idle and during reset,
        // and message type 0 is a legal VN0 encoding, so an unguarded check
        // fires before the design has done anything.
        if (vn2_valid_i) begin
          $error("l1_cache: tile %0d got VN2 message type %0d", tile_id_i, vn2_msg_i.msg_type);
        end
      end
    endcase
  end

  // The MSHR this response belongs to. It was allocated before the request was
  // sent, which is precisely why the response can always be sunk.
  logic [MSHR_ENTRIES-1:0] vn2_match;
  always_comb begin
    vn2_match = '0;
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      vn2_match[i] = mshr[i].valid && (mshr[i].addr == vn2_msg_i.addr);
    end
  end
  assign vn2_hit = |vn2_match;
  always_comb begin
    vn2_idx = '0;
    for (int unsigned i = MSHR_ENTRIES; i > 0; i--) begin
      if (vn2_match[i - 1]) vn2_idx = MSHR_IDX_W'(i - 1);
    end
  end

  // The responding line's way in the array, which is where its state lives.
  logic [L1_IDX_W-1:0] vn2_set;
  logic [L1_TAG_W-1:0] vn2_tag;
  logic [L1_WAYS-1:0]  vn2_way_mask;
  logic [L1_WAY_W-1:0] vn2_way;
  assign vn2_set = line_l1_index(vn2_msg_i.addr);
  assign vn2_tag = line_l1_tag(vn2_msg_i.addr);
  always_comb begin
    vn2_way_mask = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      vn2_way_mask[w] = (state_q[vn2_set][w] != L1_I) && (tag_q[vn2_set][w] == vn2_tag);
    end
    vn2_way = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      if (vn2_way_mask[w]) vn2_way = L1_WAY_W'(w);
    end
  end

  l1_coh_fsm u_vn2_fsm (
    .state_i (state_q[vn2_set][vn2_way]),
    .event_i (vn2_event),
    .next_state_o (vn2_next),
    .action_o (vn2_act)
  );

  // The table says IM_A and SM_A decrement and stay; turning "ack_cnt reached
  // zero" into M is the controller's job, and it is done HERE rather than at
  // retirement so that the promotion is visible to a forward arriving the next
  // cycle. It also covers the early-ack race (R1) in one place: if acks
  // overtook the data, ack_cnt is negative on arrival and the AckCount credits
  // it straight to zero, so IM_AD + Data[ack>0] promotes to M immediately
  // without ever resting in IM_A.
  logic signed [ACK_CNT_W-1:0] vn2_ack_next;
  l1_state_e                   vn2_state_eff;

  logic vn2_complete;

  always_comb begin
    vn2_ack_next = vn2_hit ? mshr[vn2_idx].ack_cnt : '0;
    if (vn2_act.ack_add) begin
      vn2_ack_next = vn2_ack_next + ACK_CNT_W'(vn2_msg_i.ack_count);
    end
    if (vn2_act.ack_dec) begin
      vn2_ack_next = vn2_ack_next - ACK_CNT_W'(1);
    end
    vn2_state_eff = vn2_next;
    vn2_complete  = vn2_act.complete;
    if (((vn2_next == L1_IM_A) || (vn2_next == L1_SM_A)) && (vn2_ack_next == '0)) begin
      vn2_state_eff = L1_M;
      vn2_complete  = 1'b1;
    end
  end

  //---------------------------------------------------------------------------
  // VN1 in: forwards, invalidations, Put-Acks. May stall.
  //---------------------------------------------------------------------------
  logic [L1_IDX_W-1:0]     vn1_set;
  logic [L1_TAG_W-1:0]     vn1_tag;
  logic [L1_WAYS-1:0]      vn1_hit_way;
  logic                    vn1_in_array;
  logic [L1_WAY_W-1:0]     vn1_way;
  logic [MSHR_ENTRIES-1:0] vn1_match;
  logic                    vn1_in_mshr;
  logic [MSHR_IDX_W-1:0]   vn1_idx;
  l1_state_e               vn1_state;
  l1_event_e               vn1_event;
  l1_state_e               vn1_next;
  l1_action_t              vn1_act;

  assign vn1_set = line_l1_index(vn1_msg_i.addr);
  assign vn1_tag = line_l1_tag(vn1_msg_i.addr);

  always_comb begin
    vn1_hit_way = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      vn1_hit_way[w] = (state_q[vn1_set][w] != L1_I) && (tag_q[vn1_set][w] == vn1_tag);
    end
    vn1_way = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      if (vn1_hit_way[w]) vn1_way = L1_WAY_W'(w);
    end
    vn1_match = '0;
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      vn1_match[i] = mshr[i].valid && (mshr[i].addr == vn1_msg_i.addr);
    end
    vn1_idx = '0;
    for (int unsigned i = MSHR_ENTRIES; i > 0; i--) begin
      if (vn1_match[i - 1]) vn1_idx = MSHR_IDX_W'(i - 1);
    end
  end

  assign vn1_in_array = |vn1_hit_way;
  // Only an EVICTION keeps its coherence state in the MSHR; every other line's
  // state, transient included, lives in the array. The two are disjoint: a
  // request to a line with a live MSHR replays, so a line can never be both
  // resident and being evicted.
  assign vn1_in_mshr = |vn1_match;
  assign vn1_state = vn1_in_mshr ? mshr[vn1_idx].state
                   : vn1_in_array ? state_q[vn1_set][vn1_way]
                   : L1_I;

  always_comb begin
    vn1_event = EV_INV;
    unique case (vn1_msg_i.msg_type)
      MSG_FWD_GETS: vn1_event = EV_FWD_GETS;
      MSG_FWD_GETM: vn1_event = EV_FWD_GETM;
      MSG_RECALL:   vn1_event = EV_FWD_GETM;   // reuses the Fwd-GetM arc
      MSG_INV:      vn1_event = EV_INV;
      MSG_PUT_ACK:  vn1_event = EV_PUT_ACK;
      default: begin
        vn1_event = EV_INV;
        if (vn1_valid_i) begin
          $error("l1_cache: tile %0d got VN1 message type %0d", tile_id_i, vn1_msg_i.msg_type);
        end
      end
    endcase
  end

  l1_coh_fsm u_vn1_fsm (
    .state_i (vn1_state),
    .event_i (vn1_event),
    .next_state_o (vn1_next),
    .action_o (vn1_act)
  );

  //---------------------------------------------------------------------------
  // Retirement
  //---------------------------------------------------------------------------
  logic [MSHR_ENTRIES-1:0] retire_req;
  logic                    retire_valid;
  logic [MSHR_IDX_W-1:0]   retire_idx;
  logic                    retire_now;

  always_comb begin
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      // A data transaction retires when the data has landed and no acks remain.
      // An eviction retires on its Put-Ack, which sets state back to I.
      retire_req[i] = mshr[i].valid && mshr[i].done;
    end
  end

  rr_arbiter #(.N(MSHR_ENTRIES)) u_retire_arb (
    .clk (clk), .rst_n (rst_n),
    .req_i (retire_req), .take_i (retire_now),
    .gnt_o (), .gnt_valid_o (retire_valid), .gnt_idx_o (retire_idx)
  );

  //---------------------------------------------------------------------------
  // Core pipeline
  //---------------------------------------------------------------------------
  logic                   s1_valid_q;
  logic [ADDR_W-1:0]      s1_addr_q;
  core_op_e               s1_op_q;
  logic [WORD_W-1:0]      s1_wdata_q;
  logic [BE_W-1:0]        s1_be_q;
  logic [CORE_TAG_W-1:0]  s1_tag_q;

  logic                   rpl_valid_q;
  logic [ADDR_W-1:0]      rpl_addr_q;
  core_op_e               rpl_op_q;
  logic [WORD_W-1:0]      rpl_wdata_q;
  logic [BE_W-1:0]        rpl_be_q;
  logic [CORE_TAG_W-1:0]  rpl_tag_q;

  logic                   s2_wr_q;
  logic [L1_IDX_W-1:0]    s2_set_q;
  logic [L1_WAY_W-1:0]    s2_way_q;
  logic [LINE_W-1:0]      s2_line_q;

  logic                   array_free;
  logic                   s0_issue;
  // Declared here rather than beside the VN1 and retire logic because
  // array_free and core_req_ready_o below depend on them, and slang enforces
  // declare-before-use even where Verilator does not.
  logic                   vn1_take;
  logic                   vn1_needs_array;
  logic                   fill_writes_array;
  logic                   s1_replay;
  logic                   s1_resp;
  logic                   s1_alloc;
  logic                   s1_evict;

  logic [ADDR_W-1:0]      s0_addr;
  core_op_e               s0_op;
  logic [WORD_W-1:0]      s0_wdata;
  logic [BE_W-1:0]        s0_be;
  logic [CORE_TAG_W-1:0]  s0_tag;

  always_comb begin
    if (rpl_valid_q) begin
      s0_addr = rpl_addr_q; s0_op = rpl_op_q; s0_wdata = rpl_wdata_q;
      s0_be = rpl_be_q; s0_tag = rpl_tag_q;
    end else begin
      s0_addr = core_addr_i; s0_op = core_op_i; s0_wdata = core_wdata_i;
      s0_be = core_be_i; s0_tag = core_tag_i;
    end
  end

  logic [L1_IDX_W-1:0]    s1_set;
  logic [L1_TAG_W-1:0]    s1_tag;
  logic [WORD_SEL_W-1:0]  s1_word;
  logic [L1_WAYS-1:0]     s1_hit_way;
  logic                   s1_hit;
  logic [L1_WAY_W-1:0]    s1_hit_idx;
  logic [L1_WAY_W-1:0]    s1_victim;
  logic                   s1_victim_ok;
  logic [L1_WAYS-1:0]     set_reserved;
  logic [LINE_W-1:0]      s1_hit_line;
  logic [LINE_W-1:0]      s1_victim_line;
  logic [LINE_W-1:0]      s1_merged;
  logic [WORD_W-1:0]      s1_rword;
  l1_state_e              s1_state;
  l1_event_e              s1_event;
  l1_state_e              s1_next;
  l1_action_t             s1_act;

  assign s1_set   = addr_l1_index(s1_addr_q);
  assign s1_tag   = addr_l1_tag(s1_addr_q);
  assign s1_word  = addr_word_sel(s1_addr_q);
  assign s1_event = (s1_op_q == OP_ST) ? EV_STORE : EV_LOAD;

  always_comb begin
    s1_hit_way = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      s1_hit_way[w] = (state_q[s1_set][w] != L1_I) && (tag_q[s1_set][w] == s1_tag);
    end
    s1_hit_idx = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      if (s1_hit_way[w]) s1_hit_idx = L1_WAY_W'(w);
    end
  end
  assign s1_hit   = |s1_hit_way;
  // The array carries the transient state too, so this is the line's real
  // state whether it is stable, filling, or upgrading.
  assign s1_state = s1_hit ? state_q[s1_set][s1_hit_idx] : L1_I;

  l1_coh_fsm u_s1_fsm (
    .state_i (s1_state), .event_i (s1_event),
    .next_state_o (s1_next), .action_o (s1_act)
  );

  always_comb begin
    set_reserved = '0;
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      if (mshr[i].valid && !mshr[i].is_evict &&
          (line_l1_index(mshr[i].addr) == s1_set)) begin
        set_reserved[mshr[i].victim_way] = 1'b1;
      end
    end
  end

  always_comb begin
    s1_victim    = L1_WAY_W'(plru_q[s1_set]);
    s1_victim_ok = 1'b0;
    if (!set_reserved[L1_WAY_W'(plru_q[s1_set])]) begin
      s1_victim    = L1_WAY_W'(plru_q[s1_set]);
      s1_victim_ok = 1'b1;
    end else begin
      for (int unsigned w = L1_WAYS; w > 0; w--) begin
        if (!set_reserved[w - 1]) begin
          s1_victim    = L1_WAY_W'(w - 1);
          s1_victim_ok = 1'b1;
        end
      end
    end
  end

  assign s1_hit_line = (s2_wr_q && (s2_set_q == s1_set) && (s2_way_q == s1_hit_idx))
                       ? s2_line_q : data_rdata[s1_hit_idx];
  assign s1_victim_line = (s2_wr_q && (s2_set_q == s1_set) && (s2_way_q == s1_victim))
                          ? s2_line_q : data_rdata[s1_victim];

  always_comb begin
    s1_merged = s1_hit_line;
    for (int unsigned b = 0; b < BE_W; b++) begin
      if (s1_be_q[b]) begin
        s1_merged[s1_word * WORD_W + b * 8 +: 8] = s1_wdata_q[b * 8 +: 8];
      end
    end
  end

  assign s1_rword = (s1_op_q == OP_ST)
                    ? s1_merged  [s1_word * WORD_W +: WORD_W]
                    : s1_hit_line[s1_word * WORD_W +: WORD_W];

  // The victim's own coherence state decides which Put it must send.
  l1_state_e    victim_state;
  l1_event_e    victim_event;
  l1_state_e    victim_next;
  l1_action_t   victim_act;
  assign victim_state = state_q[s1_set][s1_victim];
  assign victim_event = EV_EVICT;

  l1_coh_fsm u_victim_fsm (
    .state_i (victim_state), .event_i (victim_event),
    .next_state_o (victim_next), .action_o (victim_act)
  );

  logic mshr_addr_busy;
  assign mshr_lookup_addr = addr_line(s1_addr_q);
  assign mshr_addr_busy   = mshr_lookup_hit;

  // A victim that already has a transaction in flight cannot be evicted again.
  logic [MSHR_ENTRIES-1:0] victim_busy_match;
  logic                    victim_busy;
  always_comb begin
    victim_busy_match = '0;
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      victim_busy_match[i] = mshr[i].valid &&
        (mshr[i].addr == l1_line_addr(tag_q[s1_set][s1_victim], s1_set));
    end
    victim_busy = |victim_busy_match;
  end

  logic needs_evict;
  assign needs_evict = !s1_hit && s1_victim_ok &&
                       (state_q[s1_set][s1_victim] != L1_I);

  assign array_free = !s2_wr_q && !retire_now && !(vn1_take && vn1_needs_array);

  // Highest-priority work takes the pipeline's resources, so a core request is
  // only accepted when nothing above it needs them.
  // A request needs a coherence transaction whenever the table says to send a
  // GetS or a GetM. That covers a miss (from I) AND an upgrade (from S, which
  // is a tag hit), which is the case that made upgrades replay forever.
  logic s1_needs_txn;
  logic s1_is_upgrade;
  assign s1_needs_txn  = s1_act.send_gets || s1_act.send_getm;
  assign s1_is_upgrade = s1_hit && s1_needs_txn;

  assign s1_resp   = s1_valid_q && s1_act.hit;
  assign s1_evict  = s1_valid_q && s1_needs_txn && !s1_is_upgrade &&
                     s1_victim_ok && needs_evict &&
                     !victim_busy && mshr_alloc_ready && vn0_q_wr_ready;
  assign s1_alloc  = s1_valid_q && s1_needs_txn && !s1_evict &&
                     (s1_is_upgrade || (s1_victim_ok && !needs_evict)) &&
                     !mshr_addr_busy && mshr_alloc_ready && vn0_q_wr_ready;
  // A request that triggered an eviction must still REPLAY: issuing the Put
  // only frees the way, it does not serve the request. Excluding s1_evict here
  // dropped the request outright -- bug B9. The rule is simply "replay unless
  // the request was answered or got its own transaction".
  assign s1_replay = s1_valid_q && !s1_resp && !s1_alloc;

  assign core_req_ready_o = array_free && !rpl_valid_q && !s1_replay;
  assign s0_issue = array_free && (rpl_valid_q || (core_req_valid_i && core_req_ready_o));

  //---------------------------------------------------------------------------
  // MSHR allocation
  //---------------------------------------------------------------------------
  always_comb begin
    mshr_alloc_valid    = s1_evict || s1_alloc;
    mshr_alloc_is_evict = s1_evict;
    if (s1_evict) begin
      mshr_alloc_addr  = l1_line_addr(tag_q[s1_set][s1_victim], s1_set);
      mshr_alloc_state = victim_next;
      mshr_alloc_data  = s1_victim_line;
      mshr_alloc_way   = s1_victim;
    end else begin
      mshr_alloc_addr  = addr_line(s1_addr_q);
      // The MSHR's state field holds the FINAL state to install at
      // retirement, not the current one. It is filled in when the transaction
      // completes; until then the array carries the transient state.
      mshr_alloc_state = L1_I;
      mshr_alloc_data  = '0;
      mshr_alloc_way   = s1_is_upgrade ? s1_hit_idx : s1_victim;
    end
    mshr_alloc_op    = s1_op_q;
    mshr_alloc_wdata = s1_wdata_q;
    mshr_alloc_be    = s1_be_q;
    mshr_alloc_word  = s1_word;
    mshr_alloc_tag   = s1_tag_q;
  end

  //---------------------------------------------------------------------------
  // Outgoing message generation
  //---------------------------------------------------------------------------
  coh_msg_t vn0_msg_new;
  always_comb begin
    vn0_msg_new = '0;
    vn0_msg_new.src       = tile_id_i;
    vn0_msg_new.requester = tile_id_i;
    if (s1_evict) begin
      vn0_msg_new.addr = l1_line_addr(tag_q[s1_set][s1_victim], s1_set);
      vn0_msg_new.dst  = home_of(vn0_msg_new.addr);
      vn0_msg_new.data = s1_victim_line;
      if (victim_act.send_putm)      vn0_msg_new.msg_type = MSG_PUTM;
      else if (victim_act.send_pute) vn0_msg_new.msg_type = MSG_PUTE;
      else                           vn0_msg_new.msg_type = MSG_PUTS;
    end else begin
      vn0_msg_new.addr = addr_line(s1_addr_q);
      vn0_msg_new.dst  = home_of(vn0_msg_new.addr);
      vn0_msg_new.msg_type = s1_act.send_getm ? MSG_GETM : MSG_GETS;
    end
  end

  assign vn0_q_wr_valid = s1_evict || s1_alloc;
  assign vn0_q_wr_msg   = vn0_msg_new;

  // VN1 forwards are handled by a small FSM rather than combinationally, for
  // two reasons. First, M/E + Fwd-GetS has to send TWO messages -- data to the
  // requester and a refreshed copy to the directory -- and a single-cycle
  // handler cannot. Second, and less obvious, the line's data has to be READ
  // FROM THE ARRAY: the forwarded address is not the address the core pipeline
  // happened to look up, so the SRAM output is about some other line entirely.
  // The forward therefore takes the array port for a cycle, which is exactly
  // the coherence-over-core priority the deadlock argument requires.
  typedef enum logic [1:0] {
    V1_IDLE = 2'd0,
    V1_READ = 2'd1,
    V1_SEND = 2'd2
  } vn1_fsm_e;

  vn1_fsm_e              vn1_fsm_q;
  coh_msg_t              vn1_msg_q;
  logic [LINE_W-1:0]     vn1_line_q;
  logic [L1_WAY_W-1:0]   vn1_way_q;
  logic                  vn1_send_req_q;   // data to the requester
  logic                  vn1_send_dir_q;   // refreshed copy to the directory
  logic                  vn1_send_ack_q;   // Inv-Ack

  logic vn1_needs_line;

  assign vn1_needs_line  = vn1_act.send_data_req || vn1_act.send_data_dir;
  assign vn1_needs_array = vn1_needs_line && !vn1_in_mshr;

  // A forward is accepted only when everything it will need is available.
  assign vn1_take = (vn1_fsm_q == V1_IDLE) && vn1_valid_i &&
                    !vn1_act.stall && !vn1_act.illegal &&
                    !(vn2_take && vn2_hit) &&
                    !retire_now &&
                    (!vn1_needs_array || (!s2_wr_q && !fill_writes_array));

  assign vn1_ready_o = vn1_take;

  // Outgoing VN2 message, driven from the latched forward.
  coh_msg_t vn2_msg_new;
  always_comb begin
    vn2_msg_new = '0;
    vn2_msg_new.addr      = vn1_msg_q.addr;
    vn2_msg_new.src       = tile_id_i;
    vn2_msg_new.requester = tile_id_i;
    vn2_msg_new.data      = vn1_line_q;
    if (vn1_send_ack_q) begin
      vn2_msg_new.msg_type = MSG_INV_ACK;
      vn2_msg_new.dst      = vn1_msg_q.requester;
      vn2_msg_new.data     = '0;
    end else if (vn1_send_req_q) begin
      vn2_msg_new.msg_type = MSG_DATA_OWNER;
      vn2_msg_new.dst      = vn1_msg_q.requester;
    end else begin
      // The directory's refreshed copy. It must be sent unconditionally on a
      // Fwd-GetS from E or M, because the directory cannot tell whether this
      // core silently upgraded E->M.
      vn2_msg_new.msg_type = MSG_WB_DATA;
      vn2_msg_new.dst      = home_of(vn1_msg_q.addr);
    end
  end

  logic vn1_has_send;
  assign vn1_has_send = vn1_send_ack_q || vn1_send_req_q || vn1_send_dir_q;

  assign vn2_q_wr_valid = (vn1_fsm_q == V1_SEND) && vn1_has_send;
  assign vn2_q_wr_msg   = vn2_msg_new;

  //---------------------------------------------------------------------------
  // Retire / fill
  //---------------------------------------------------------------------------
  logic [L1_IDX_W-1:0]   fill_set;
  logic [L1_WAY_W-1:0]   fill_way;
  logic [WORD_SEL_W-1:0] fill_word;
  logic [LINE_W-1:0]     fill_line;

  assign fill_set  = line_l1_index(mshr[retire_idx].addr);
  assign fill_way  = mshr[retire_idx].victim_way;
  assign fill_word = mshr[retire_idx].word_sel;

  always_comb begin
    fill_line = mshr[retire_idx].data;
    if (!mshr[retire_idx].is_evict && (mshr[retire_idx].op == OP_ST)) begin
      for (int unsigned b = 0; b < BE_W; b++) begin
        if (mshr[retire_idx].be[b]) begin
          fill_line[fill_word * WORD_W + b * 8 +: 8] = mshr[retire_idx].wdata[b * 8 +: 8];
        end
      end
    end
  end

  assign retire_now = retire_valid && !s2_wr_q && !s1_resp;

  //---------------------------------------------------------------------------
  // Data array arbitration: S2 store write, then fill, then lookup.
  //---------------------------------------------------------------------------
  // An upgrade's line is already resident with its data; only a fill brings
  // new data that has to be written.
  logic retire_is_fill;
  assign retire_is_fill = retire_now && !mshr[retire_idx].is_evict &&
                          mshr[retire_idx].data_valid;
  assign fill_writes_array = retire_is_fill;

  always_comb begin
    data_addr  = addr_l1_index(s0_addr);
    data_wdata = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      data_en[w] = 1'b0;
      data_we[w] = 1'b0;
    end
    if (s2_wr_q) begin
      data_addr = s2_set_q; data_wdata = s2_line_q;
      data_en[s2_way_q] = 1'b1; data_we[s2_way_q] = 1'b1;
    end else if (fill_writes_array) begin
      data_addr = fill_set; data_wdata = fill_line;
      data_en[fill_way] = 1'b1; data_we[fill_way] = 1'b1;
    end else if (vn1_take && vn1_needs_array) begin
      // A forward reads the line it names, which is not the line the core
      // pipeline looked up. Coherence outranks the core request, so S0 simply
      // does not issue this cycle.
      data_addr = vn1_set;
      for (int unsigned w = 0; w < L1_WAYS; w++) data_en[w] = 1'b1;
    end else if (s0_issue) begin
      data_addr = addr_l1_index(s0_addr);
      for (int unsigned w = 0; w < L1_WAYS; w++) data_en[w] = 1'b1;
    end
  end

  //---------------------------------------------------------------------------
  // MSHR update arbitration: VN2 first (it cannot be refused), then VN1.
  //---------------------------------------------------------------------------
  always_comb begin
    mshr_upd_valid     = 1'b0;
    mshr_upd_idx       = '0;
    mshr_upd_set_state = 1'b0;
    mshr_upd_state     = L1_I;
    mshr_upd_set_data  = 1'b0;
    mshr_upd_data      = '0;
    mshr_upd_ack_dec   = 1'b0;
    mshr_upd_ack_add   = 1'b0;
    mshr_upd_ack_val   = '0;
    mshr_upd_done      = 1'b0;

    if (vn2_take && vn2_hit) begin
      mshr_upd_valid     = 1'b1;
      mshr_upd_idx       = vn2_idx;
      // Only the FINAL state is recorded, and only once the transaction is
      // complete. Until then the array holds the transient state.
      mshr_upd_set_state = vn2_complete;
      mshr_upd_state     = vn2_state_eff;
      mshr_upd_done      = vn2_complete;
      mshr_upd_set_data  = vn2_act.fill_data;
      mshr_upd_data      = vn2_msg_i.data;
      mshr_upd_ack_dec   = vn2_act.ack_dec;
      mshr_upd_ack_add   = vn2_act.ack_add;
      mshr_upd_ack_val   = ACK_CNT_W'(vn2_msg_i.ack_count);
    end else if (vn1_take && vn1_in_mshr) begin
      // Only an eviction keeps its state in the MSHR.
      mshr_upd_valid     = 1'b1;
      mshr_upd_idx       = vn1_idx;
      mshr_upd_set_state = 1'b1;
      mshr_upd_state     = vn1_next;
      mshr_upd_done      = vn1_act.complete;
    end
  end

  assign mshr_free_valid = retire_now;
  assign mshr_free_idx   = retire_idx;

  //---------------------------------------------------------------------------
  // Sequential
  //---------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      s1_valid_q <= 1'b0; s1_addr_q <= '0; s1_op_q <= OP_LD;
      s1_wdata_q <= '0; s1_be_q <= '0; s1_tag_q <= '0;
      rpl_valid_q <= 1'b0; rpl_addr_q <= '0; rpl_op_q <= OP_LD;
      rpl_wdata_q <= '0; rpl_be_q <= '0; rpl_tag_q <= '0;
      s2_wr_q <= 1'b0; s2_set_q <= '0; s2_way_q <= '0; s2_line_q <= '0;
      vn1_fsm_q <= V1_IDLE; vn1_msg_q <= '0; vn1_line_q <= '0;
      vn1_way_q <= '0; vn1_send_req_q <= 1'b0; vn1_send_dir_q <= 1'b0;
      vn1_send_ack_q <= 1'b0;
      core_resp_valid_o <= 1'b0; core_resp_tag_o <= '0; core_resp_rdata_o <= '0;
      for (int unsigned s = 0; s < L1_SETS; s++) begin
        plru_q[s] <= 1'b0;
        for (int unsigned w = 0; w < L1_WAYS; w++) begin
          state_q[s][w] <= L1_I;
          tag_q[s][w]   <= '0;
        end
      end
    end else begin
      core_resp_valid_o <= 1'b0;
      s2_wr_q           <= 1'b0;
      if (rpl_valid_q && s0_issue) rpl_valid_q <= 1'b0;

      // ---- VN1 forward FSM ----
      unique case (vn1_fsm_q)
        V1_IDLE: begin
          if (vn1_take) begin
            vn1_msg_q      <= vn1_msg_i;
            vn1_way_q      <= vn1_way;
            vn1_send_req_q <= vn1_act.send_data_req;
            vn1_send_dir_q <= vn1_act.send_data_dir;
            vn1_send_ack_q <= vn1_act.send_inv_ack;
            // The state update happens on acceptance, so a second forward for
            // the same line sees the new state.
            if (!vn1_in_mshr && vn1_in_array) begin
              state_q[vn1_set][vn1_way] <= vn1_next;
            end
            if (vn1_needs_line && vn1_in_mshr) begin
              vn1_line_q <= mshr[vn1_idx].data;
              vn1_fsm_q  <= V1_SEND;
            end else if (vn1_needs_array) begin
              vn1_fsm_q <= V1_READ;
            end else if (vn1_act.send_inv_ack) begin
              vn1_fsm_q <= V1_SEND;
            end
            // Put-Ack and anything else with no send retires here.
          end
        end

        V1_READ: begin
          vn1_line_q <= data_rdata[vn1_way_q];
          vn1_fsm_q  <= V1_SEND;
        end

        V1_SEND: begin
          if (vn1_has_send) begin
            if (vn2_q_wr_ready) begin
              if (vn1_send_ack_q)      vn1_send_ack_q <= 1'b0;
              else if (vn1_send_req_q) vn1_send_req_q <= 1'b0;
              else                     vn1_send_dir_q <= 1'b0;
            end
          end else begin
            vn1_fsm_q <= V1_IDLE;
          end
        end

        default: begin
          vn1_fsm_q <= V1_IDLE;
          $error("l1_cache: tile %0d illegal VN1 state", tile_id_i);
        end
      endcase

      // ---- VN2: advance the array's transient state ----
      if (vn2_take && vn2_hit && !vn2_complete) begin
        state_q[vn2_set][vn2_way] <= vn2_state_eff;
      end

      // ---- Retire ----
      if (retire_now) begin
        if (!mshr[retire_idx].is_evict) begin
          // Data and the stable state land together, so nothing can observe a
          // line whose state says M while its data is still the old line's.
          state_q[fill_set][fill_way] <= mshr[retire_idx].state;
          tag_q[fill_set][fill_way]   <= line_l1_tag(mshr[retire_idx].addr);
          plru_q[fill_set]            <= ~fill_way[0];
          core_resp_valid_o           <= 1'b1;
          core_resp_tag_o             <= mshr[retire_idx].core_tag;
          core_resp_rdata_o           <= fill_line[fill_word * WORD_W +: WORD_W];
        end
      end

      // ---- S1 ----
      if (s1_resp) begin
        plru_q[s1_set]    <= ~s1_hit_idx[0];
        core_resp_valid_o <= 1'b1;
        core_resp_tag_o   <= s1_tag_q;
        core_resp_rdata_o <= s1_rword;
        state_q[s1_set][s1_hit_idx] <= s1_next;
        if (s1_op_q == OP_ST) begin
          s2_wr_q   <= 1'b1;
          s2_set_q  <= s1_set;
          s2_way_q  <= s1_hit_idx;
          s2_line_q <= s1_merged;
        end
      end

      if (s1_evict) begin
        // The victim is gone from the array the moment its Put is issued; its
        // transient state moves into the eviction MSHR.
        state_q[s1_set][s1_victim] <= L1_I;
        plru_q[s1_set]             <= ~s1_victim[0];
      end

      if (s1_alloc) begin
        if (s1_is_upgrade) begin
          // The line stays resident and readable; only its state moves.
          state_q[s1_set][s1_hit_idx] <= s1_next;
          plru_q[s1_set]              <= ~s1_hit_idx[0];
        end else begin
          // Claim the way now, with the transient state, so a forward for this
          // line finds it while the fill is in flight.
          tag_q[s1_set][s1_victim]   <= s1_tag;
          state_q[s1_set][s1_victim] <= s1_next;
          plru_q[s1_set]             <= ~s1_victim[0];
        end
      end

      if (s1_replay) begin
        rpl_valid_q <= 1'b1;
        rpl_addr_q  <= s1_addr_q; rpl_op_q <= s1_op_q;
        rpl_wdata_q <= s1_wdata_q; rpl_be_q <= s1_be_q; rpl_tag_q <= s1_tag_q;
      end

      // ---- S0 -> S1 ----
      if (array_free) begin
        s1_valid_q <= s0_issue;
        if (s0_issue) begin
          s1_addr_q <= s0_addr; s1_op_q <= s0_op; s1_wdata_q <= s0_wdata;
          s1_be_q <= s0_be; s1_tag_q <= s0_tag;
        end
      end else begin
        s1_valid_q <= 1'b0;
      end
    end
  end

  always_comb begin
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      dbg_state_o[w] = 4'(state_q[dbg_set_i][w]);
      dbg_tag_o[w]   = tag_q[dbg_set_i][w];
    end
  end
  always_comb begin
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      dbg_mshr_valid_o[i] = mshr[i].valid;
    end
  end

`ifndef SYNTHESIS
  a_one_hit_way : assert property (@(posedge clk) disable iff (!rst_n)
    s1_valid_q |-> $onehot0(s1_hit_way))
    else $error("l1_cache: tile %0d has one line in two ways at set %0d", tile_id_i, s1_set);

  a_vn2_event_legal : assert property (@(posedge clk) disable iff (!rst_n)
    (vn2_take && vn2_hit) |-> !vn2_act.illegal)
    else $error("l1_cache: tile %0d took VN2 event %0d in state %0d, which the table marks impossible", tile_id_i, vn2_event, mshr[vn2_idx].state);

  a_vn1_event_legal : assert property (@(posedge clk) disable iff (!rst_n)
    vn1_valid_i |-> !vn1_act.illegal)
    else $error("l1_cache: tile %0d took VN1 event %0d in state %0d, which the table marks impossible -- if this is a forward into II_A it is race R8 and the bug is at the directory", tile_id_i, vn1_event, vn1_state);

  a_vn2_always_lands : assert property (@(posedge clk) disable iff (!rst_n)
    vn2_take |-> vn2_hit)
    else $error("l1_cache: tile %0d got a VN2 response for line %0h with no MSHR -- the response has nowhere to go", tile_id_i, vn2_msg_i.addr);

  // $signed is not decoration. A packed struct is unsigned as a whole, and a
  // member read out of one does not reliably carry its own signedness, so
  // `ack_cnt <= 0` compares a two's-complement -1 as 15 and fires on exactly
  // the early-ack case this design is built around. The protocol itself was
  // never affected because completion tests `== 0`, which is sign-agnostic --
  // only the comparison here was wrong. Bug B13.
  a_ack_never_positive_after_data : assert property (@(posedge clk) disable iff (!rst_n)
    (vn2_take && vn2_hit && vn2_act.ack_add) |-> ($signed(mshr[vn2_idx].ack_cnt) <= 0))
    else $error("l1_cache: tile %0d credited an AckCount onto a count of %0d -- Data arrived twice", tile_id_i, $signed(mshr[vn2_idx].ack_cnt));
`endif

endmodule : l1_cache
