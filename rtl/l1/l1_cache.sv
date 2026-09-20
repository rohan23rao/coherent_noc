//=============================================================================
// l1_cache.sv
//
// Private L1 data cache: 64 sets x 2 ways x 32 B, PIPT, write-back /
// write-allocate, NON-BLOCKING with an MSHR_ENTRIES-deep MSHR file. At Phase 5
// there is still no coherence: misses are served by a directly attached
// memory. Coherence replaces the memory interface in Phase 6.
//
// Three stages:
//   S0  pick a request (replay first, else the core), issue the data read
//   S1  compare tags; hit responds, miss allocates an MSHR or replays
//   S2  write the data array (store hit)
// plus two background engines: a memory engine that walks the MSHRs issuing
// writebacks and fetches, and a fill engine that writes arrays and retires.
//
// Interfaces: clk, rst_n; core request/response; the line-granular memory
// channel; a combinational debug bus exposing per-way state and tag.
//
// Four non-obvious things.
//
// 1. A request that cannot make progress in S1 -- MSHR full, or a secondary
//    miss to a line that already has one -- is REPLAYED through S0 rather than
//    held in S1. Holding it would leave it looking at array data that a fill
//    may have overwritten underneath it; replaying re-reads the array and
//    re-compares against current state, so there is no staleness to reason
//    about.
//
// 2. The MSHR's `data` field does double duty. Between allocation and the
//    writeback completing it holds the *victim's* dirty line; after that it
//    holds the incoming fill. The two never overlap because the per-MSHR
//    sequence is strictly writeback-then-fetch, and reusing the field saves
//    256 bits per entry over carrying both.
//
// 3. The victim's line address is not stored. It is reconstructed at writeback
//    time from tag_q and the set, which is safe precisely because the fill --
//    the only thing that overwrites that tag -- cannot run until the writeback
//    has completed.
//
// 4. Three things want the single-ported data array: the S2 store write, the
//    fill, and a new lookup, in that priority order. The S2 write is already
//    committed (its response has been sent) so it cannot be deferred; a fill
//    can wait a cycle; a lookup is simply not accepted.
//=============================================================================

module l1_cache
  import coh_pkg::*;
(
  input  logic                        clk,
  input  logic                        rst_n,

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

  output logic                        mem_req_valid_o,
  input  logic                        mem_req_ready_i,
  output logic [LINE_ADDR_W-1:0]      mem_req_addr_o,
  output logic                        mem_req_we_o,
  output logic [LINE_W-1:0]           mem_req_wdata_o,
  input  logic                        mem_resp_valid_i,
  input  logic [LINE_W-1:0]           mem_resp_rdata_i,

  input  logic [L1_IDX_W-1:0]         dbg_set_i,
  input  logic [L1_WAY_W-1:0]         dbg_way_i,
  output l1_state_e                   dbg_state_o,
  output logic [L1_TAG_W-1:0]         dbg_tag_o,
  output logic [MSHR_ENTRIES-1:0]     dbg_mshr_valid_o
);

  //---------------------------------------------------------------------------
  // Arrays: tag and state in flops (decision D10), data in SRAM.
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
    sram_1rw #(
      .WIDTH (LINE_W),
      .DEPTH (L1_SETS)
    ) u_data (
      .clk     (clk),
      .en_i    (data_en[w]),
      .we_i    (data_we[w]),
      .addr_i  (data_addr),
      .wdata_i (data_wdata),
      .rdata_o (data_rdata[w])
    );
  end

  //---------------------------------------------------------------------------
  // Pipeline registers
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

  // Did a fill write the array last cycle, and to which set?
  logic                   fill_done_q;
  logic [L1_IDX_W-1:0]    fill_set_q;

  //---------------------------------------------------------------------------
  // MSHR file
  //---------------------------------------------------------------------------
  logic                    mshr_alloc_valid;
  logic                    mshr_alloc_ready;
  logic [LINE_ADDR_W-1:0]  mshr_alloc_addr;
  l1_state_e               mshr_alloc_state;
  logic [L1_WAY_W-1:0]     mshr_alloc_way;
  logic                    mshr_alloc_needs_wb;

  logic                    mshr_lookup_hit;

  logic                    mshr_upd_data_valid;
  logic [MSHR_IDX_W-1:0]   mshr_upd_data_idx;
  logic [LINE_W-1:0]       mshr_upd_data;

  logic                    mshr_wb_done;
  logic [MSHR_IDX_W-1:0]   mshr_wb_idx;

  logic                    mshr_free_valid;
  logic [MSHR_IDX_W-1:0]   mshr_free_idx;

  mshr_e [MSHR_ENTRIES-1:0] mshr;

  //---------------------------------------------------------------------------
  // S0: request selection
  //---------------------------------------------------------------------------
  logic                   fill_now;
  logic                   array_free;
  logic                   s0_issue;
  // Declared here rather than with the rest of the S1 signals because
  // core_req_ready_o below depends on them.
  logic                   s1_replay;
  logic                   s1_resp;
  logic                   s1_alloc;
  logic [ADDR_W-1:0]      s0_addr;
  core_op_e               s0_op;
  logic [WORD_W-1:0]      s0_wdata;
  logic [BE_W-1:0]        s0_be;
  logic [CORE_TAG_W-1:0]  s0_tag;

  assign array_free = !s2_wr_q && !fill_now;

  always_comb begin
    if (rpl_valid_q) begin
      s0_addr  = rpl_addr_q;
      s0_op    = rpl_op_q;
      s0_wdata = rpl_wdata_q;
      s0_be    = rpl_be_q;
      s0_tag   = rpl_tag_q;
    end else begin
      s0_addr  = core_addr_i;
      s0_op    = core_op_i;
      s0_wdata = core_wdata_i;
      s0_be    = core_be_i;
      s0_tag   = core_tag_i;
    end
  end

  // A new core request must also be refused on a cycle where the request
  // currently in S1 is about to replay. The replay slot holds exactly one
  // request, so accepting a second here would let the S1 request overwrite the
  // slot's occupant on a later cycle and lose it outright -- bug B5. Refusing
  // costs one issue slot per replay and makes the invariant
  // `s1_replay |-> !rpl_valid_q` hold, which is asserted below.
  assign core_req_ready_o = array_free && !rpl_valid_q && !s1_replay;
  assign s0_issue         = array_free && (rpl_valid_q || (core_req_valid_i && core_req_ready_o));

  //---------------------------------------------------------------------------
  // S1: tag compare
  //---------------------------------------------------------------------------
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
  logic                   s1_same_set_fill;

  assign s1_set    = addr_l1_index(s1_addr_q);
  assign s1_tag    = addr_l1_tag(s1_addr_q);
  assign s1_word   = addr_word_sel(s1_addr_q);

  // Ways in this set already reserved by a live MSHR. With MSHR_ENTRIES=4 and
  // L1_WAYS=2, three concurrent misses to one set would otherwise hand the same
  // victim way to two MSHRs -- pseudo-LRU flips back after two allocations --
  // and the second fill would silently overwrite the first. Allocation must
  // pick an unreserved way or replay. See docs/decisions.md D12.
  always_comb begin
    set_reserved = '0;
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      if (mshr[i].valid && (line_l1_index(mshr[i].addr) == s1_set)) begin
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

  always_comb begin
    s1_hit_way = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      s1_hit_way[w] = (state_q[s1_set][w] != L1_I) && (tag_q[s1_set][w] == s1_tag);
    end
  end

  assign s1_hit = |s1_hit_way;

  always_comb begin
    s1_hit_idx = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      if (s1_hit_way[w]) begin
        s1_hit_idx = L1_WAY_W'(w);
      end
    end
  end

  // S2 forwarding: the pending store write has not reached the array, so a
  // read issued in S0 sees pre-store data. Both the hit line and the victim
  // line need it -- the victim case is a store hit on the way that is about to
  // be evicted, where missing the forward would write a stale line back to
  // memory and lose the store.
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

  // A store's response is the word after the store (bug B4).
  assign s1_rword = (s1_op_q == OP_ST)
                    ? s1_merged  [s1_word * WORD_W +: WORD_W]
                    : s1_hit_line[s1_word * WORD_W +: WORD_W];

  // A fill that landed on this set last cycle invalidates the array read this
  // request issued in S0. Replay rather than reason about which way moved.
  assign s1_same_set_fill = fill_done_q && (fill_set_q == s1_set);

  assign s1_replay = s1_valid_q &&
                     (s1_same_set_fill ||
                      (!s1_hit && (mshr_lookup_hit || !mshr_alloc_ready ||
                                   !s1_victim_ok)));

  assign s1_resp  = s1_valid_q && !s1_same_set_fill && s1_hit;
  assign s1_alloc = s1_valid_q && !s1_same_set_fill && !s1_hit &&
                    !mshr_lookup_hit && mshr_alloc_ready && s1_victim_ok;

  assign mshr_alloc_valid    = s1_alloc;
  assign mshr_alloc_addr     = addr_line(s1_addr_q);
  assign mshr_alloc_state    = (s1_op_q == OP_ST) ? L1_IM_AD : L1_IS_D;
  assign mshr_alloc_way      = s1_victim;
  assign mshr_alloc_needs_wb = (state_q[s1_set][s1_victim] == L1_M);

  //---------------------------------------------------------------------------
  // Memory engine: pick an MSHR needing a writeback or a fetch.
  //---------------------------------------------------------------------------
  typedef enum logic [1:0] {
    MEM_IDLE = 2'd0,
    MEM_WB   = 2'd1,
    MEM_FET  = 2'd2
  } mem_state_e;

  mem_state_e            mem_q;
  logic [MSHR_IDX_W-1:0] mem_idx_q;

  logic [MSHR_ENTRIES-1:0] wb_req;
  logic [MSHR_ENTRIES-1:0] fet_req;
  logic [MSHR_ENTRIES-1:0] mem_req_mask;
  logic                    mem_gnt_valid;
  logic [MSHR_IDX_W-1:0]   mem_gnt_idx;

  // A fetch must not overtake a pending writeback of the SAME line. That can
  // happen because the victim is invalidated at allocate, so a new request for
  // the evicted line misses and allocates its own MSHR while the writeback is
  // still queued -- and the fetch would then read pre-writeback memory.
  logic [MSHR_ENTRIES-1:0] wb_conflict;

  always_comb begin
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      wb_conflict[i] = 1'b0;
      for (int unsigned j = 0; j < MSHR_ENTRIES; j++) begin
        if ((j != i) && mshr[j].valid && mshr[j].needs_wb &&
            (mshr[j].wb_addr == mshr[i].addr)) begin
          wb_conflict[i] = 1'b1;
        end
      end
    end
  end

  always_comb begin
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      wb_req[i]  = mshr[i].valid && mshr[i].needs_wb;
      fet_req[i] = mshr[i].valid && !mshr[i].needs_wb && !mshr[i].data_valid
                   && !wb_conflict[i];
    end
    mem_req_mask = wb_req | fet_req;
  end

  rr_arbiter #(
    .N (MSHR_ENTRIES)
  ) u_mem_arb (
    .clk         (clk),
    .rst_n       (rst_n),
    .req_i       (mem_req_mask),
    .take_i      ((mem_q == MEM_IDLE) && mem_req_ready_i),
    .gnt_o       (),
    .gnt_valid_o (mem_gnt_valid),
    .gnt_idx_o   (mem_gnt_idx)
  );

  logic sel_is_wb;
  assign sel_is_wb = wb_req[mem_gnt_idx];

  always_comb begin
    mem_req_valid_o = 1'b0;
    mem_req_addr_o  = '0;
    mem_req_we_o    = 1'b0;
    mem_req_wdata_o = '0;
    if ((mem_q == MEM_IDLE) && mem_gnt_valid) begin
      mem_req_valid_o = 1'b1;
      if (sel_is_wb) begin
        // Captured at allocate: the tag can no longer be trusted here, because
        // the victim was invalidated and its way may already have been refilled.
        mem_req_addr_o  = mshr[mem_gnt_idx].wb_addr;
        mem_req_we_o    = 1'b1;
        mem_req_wdata_o = mshr[mem_gnt_idx].data;
      end else begin
        mem_req_addr_o = mshr[mem_gnt_idx].addr;
        mem_req_we_o   = 1'b0;
      end
    end
  end

  //---------------------------------------------------------------------------
  // Fill engine: an MSHR whose data has arrived writes the arrays and retires.
  //---------------------------------------------------------------------------
  logic [MSHR_ENTRIES-1:0] fill_req;
  logic                    fill_gnt_valid;
  logic [MSHR_IDX_W-1:0]   fill_idx;

  always_comb begin
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      fill_req[i] = mshr[i].valid && mshr[i].data_valid && !mshr[i].needs_wb;
    end
  end

  rr_arbiter #(
    .N (MSHR_ENTRIES)
  ) u_fill_arb (
    .clk         (clk),
    .rst_n       (rst_n),
    .req_i       (fill_req),
    .take_i      (fill_now),
    .gnt_o       (),
    .gnt_valid_o (fill_gnt_valid),
    .gnt_idx_o   (fill_idx)
  );

  // A fill both writes the array and sends a response, so it must not collide
  // with the committed S2 write or with an S1 hit response.
  assign fill_now = fill_gnt_valid && !s2_wr_q && !s1_resp;

  logic [L1_IDX_W-1:0]   fill_set;
  logic [WORD_SEL_W-1:0] fill_word;
  logic [L1_WAY_W-1:0]   fill_way;
  logic [LINE_W-1:0]     fill_line;

  assign fill_set  = line_l1_index(mshr[fill_idx].addr);
  assign fill_way  = mshr[fill_idx].victim_way;
  // The MSHR holds a LINE address, so the word the core asked for has to be
  // remembered explicitly -- it is not recoverable from the address.
  assign fill_word = mshr[fill_idx].word_sel;

  always_comb begin
    fill_line = mshr[fill_idx].data;
    if (mshr[fill_idx].op == OP_ST) begin
      for (int unsigned b = 0; b < BE_W; b++) begin
        if (mshr[fill_idx].be[b]) begin
          fill_line[fill_word * WORD_W + b * 8 +: 8] = mshr[fill_idx].wdata[b * 8 +: 8];
        end
      end
    end
  end

  //---------------------------------------------------------------------------
  // Data array arbitration: S2 write, then fill, then lookup.
  //---------------------------------------------------------------------------
  always_comb begin
    data_addr  = addr_l1_index(s0_addr);
    data_wdata = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      data_en[w] = 1'b0;
      data_we[w] = 1'b0;
    end

    if (s2_wr_q) begin
      data_addr         = s2_set_q;
      data_wdata        = s2_line_q;
      data_en[s2_way_q] = 1'b1;
      data_we[s2_way_q] = 1'b1;
    end else if (fill_now) begin
      data_addr          = fill_set;
      data_wdata         = fill_line;
      data_en[fill_way]  = 1'b1;
      data_we[fill_way]  = 1'b1;
    end else if (s0_issue) begin
      data_addr = addr_l1_index(s0_addr);
      for (int unsigned w = 0; w < L1_WAYS; w++) begin
        data_en[w] = 1'b1;
      end
    end
  end

  //---------------------------------------------------------------------------
  // MSHR update wiring
  //---------------------------------------------------------------------------
  assign mshr_upd_data_valid = (mem_q == MEM_FET) && mem_resp_valid_i;
  assign mshr_upd_data_idx   = mem_idx_q;
  assign mshr_upd_data       = mem_resp_rdata_i;
  assign mshr_wb_done        = (mem_q == MEM_WB) && mem_resp_valid_i;
  assign mshr_wb_idx         = mem_idx_q;
  assign mshr_free_valid     = fill_now;
  assign mshr_free_idx       = fill_idx;

  mshr_file u_mshr (
    .clk               (clk),
    .rst_n             (rst_n),
    .alloc_valid_i     (mshr_alloc_valid),
    .alloc_ready_o     (mshr_alloc_ready),
    .alloc_addr_i      (mshr_alloc_addr),
    .alloc_state_i     (mshr_alloc_state),
    .alloc_op_i        (s1_op_q),
    .alloc_wdata_i     (s1_wdata_q),
    .alloc_be_i        (s1_be_q),
    .alloc_word_sel_i  (s1_word),
    .alloc_tag_i       (s1_tag_q),
    .alloc_way_i       (mshr_alloc_way),
    .alloc_needs_wb_i  (mshr_alloc_needs_wb),
    .alloc_wb_data_i   (s1_victim_line),
    .alloc_wb_addr_i   (l1_line_addr(tag_q[s1_set][s1_victim], s1_set)),
    .alloc_idx_o       (),
    .lookup_addr_i     (addr_line(s1_addr_q)),
    .lookup_hit_o      (mshr_lookup_hit),
    .lookup_idx_o      (),
    .upd_state_valid_i (1'b0),
    .upd_state_idx_i   ('0),
    .upd_state_i       (L1_I),
    .upd_data_valid_i  (mshr_upd_data_valid),
    .upd_data_idx_i    (mshr_upd_data_idx),
    .upd_data_i        (mshr_upd_data),
    .upd_wb_done_i     (mshr_wb_done),
    .upd_wb_idx_i      (mshr_wb_idx),
    .free_valid_i      (mshr_free_valid),
    .free_idx_i        (mshr_free_idx),
    .entries_o         (mshr)
  );

  //---------------------------------------------------------------------------
  // Sequential
  //---------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      s1_valid_q        <= 1'b0;
      s1_addr_q         <= '0;
      s1_op_q           <= OP_LD;
      s1_wdata_q        <= '0;
      s1_be_q           <= '0;
      s1_tag_q          <= '0;
      rpl_valid_q       <= 1'b0;
      rpl_addr_q        <= '0;
      rpl_op_q          <= OP_LD;
      rpl_wdata_q       <= '0;
      rpl_be_q          <= '0;
      rpl_tag_q         <= '0;
      s2_wr_q           <= 1'b0;
      s2_set_q          <= '0;
      s2_way_q          <= '0;
      s2_line_q         <= '0;
      fill_done_q       <= 1'b0;
      fill_set_q        <= '0;
      core_resp_valid_o <= 1'b0;
      core_resp_tag_o   <= '0;
      core_resp_rdata_o <= '0;
      mem_q             <= MEM_IDLE;
      mem_idx_q         <= '0;
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
      fill_done_q       <= 1'b0;

      // The replay slot holds its occupant until that request is actually
      // re-issued into S0. Clearing it unconditionally and relying on S1 to
      // re-set it drops the request on any cycle where the array is busy and
      // it cannot be issued -- bug B5.
      if (rpl_valid_q && s0_issue) begin
        rpl_valid_q <= 1'b0;
      end

      // ---- S1 ----
      if (s1_resp) begin
        plru_q[s1_set]    <= ~s1_hit_idx[0];
        core_resp_valid_o <= 1'b1;
        core_resp_tag_o   <= s1_tag_q;
        core_resp_rdata_o <= s1_rword;
        if (s1_op_q == OP_ST) begin
          s2_wr_q   <= 1'b1;
          s2_set_q  <= s1_set;
          s2_way_q  <= s1_hit_idx;
          s2_line_q <= s1_merged;
          state_q[s1_set][s1_hit_idx] <= L1_M;
        end
      end

      if (s1_alloc) begin
        // Reserve the way, and INVALIDATE the victim immediately. Leaving it
        // valid would let a later store hit the line that is already being
        // written back -- the store would land in the array, the writeback
        // would carry the data captured here (pre-store), and the fill would
        // then overwrite the way. The store is lost twice over. Bug B6.
        plru_q[s1_set]              <= ~s1_victim[0];
        state_q[s1_set][s1_victim]  <= L1_I;
      end

      if (s1_replay) begin
        rpl_valid_q <= 1'b1;
        rpl_addr_q  <= s1_addr_q;
        rpl_op_q    <= s1_op_q;
        rpl_wdata_q <= s1_wdata_q;
        rpl_be_q    <= s1_be_q;
        rpl_tag_q   <= s1_tag_q;
      end

      // ---- S0 -> S1 ----
      s1_valid_q <= s0_issue;
      if (s0_issue) begin
        s1_addr_q  <= s0_addr;
        s1_op_q    <= s0_op;
        s1_wdata_q <= s0_wdata;
        s1_be_q    <= s0_be;
        s1_tag_q   <= s0_tag;
      end

      // ---- Memory engine ----
      unique case (mem_q)
        MEM_IDLE: begin
          if (mem_gnt_valid && mem_req_ready_i) begin
            mem_idx_q <= mem_gnt_idx;
            mem_q     <= sel_is_wb ? MEM_WB : MEM_FET;
          end
        end
        MEM_WB: begin
          if (mem_resp_valid_i) begin
            mem_q <= MEM_IDLE;
          end
        end
        MEM_FET: begin
          if (mem_resp_valid_i) begin
            mem_q <= MEM_IDLE;
          end
        end
        default: begin
          mem_q <= MEM_IDLE;
          $error("l1_cache: illegal memory-engine state %0d", mem_q);
        end
      endcase

      // ---- Fill ----
      if (fill_now) begin
        state_q[fill_set][fill_way] <= (mshr[fill_idx].op == OP_ST) ? L1_M : L1_E;
        tag_q[fill_set][fill_way]   <= line_l1_tag(mshr[fill_idx].addr);
        fill_done_q                 <= 1'b1;
        fill_set_q                  <= fill_set;
        core_resp_valid_o           <= 1'b1;
        core_resp_tag_o             <= mshr[fill_idx].core_tag;
        core_resp_rdata_o           <= fill_line[fill_word * WORD_W +: WORD_W];
      end
    end
  end

  //---------------------------------------------------------------------------
  // Debug bus
  //---------------------------------------------------------------------------
  assign dbg_state_o = state_q[dbg_set_i][dbg_way_i];
  assign dbg_tag_o   = tag_q[dbg_set_i][dbg_way_i];
  always_comb begin
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      dbg_mshr_valid_o[i] = mshr[i].valid;
    end
  end

`ifndef SYNTHESIS
  a_one_hit_way : assert property (@(posedge clk) disable iff (!rst_n)
    s1_valid_q |-> $onehot0(s1_hit_way))
    else $error("l1_cache: the same line is resident in two ways at set %0d", s1_set);

  a_resp_known : assert property (@(posedge clk) disable iff (!rst_n)
    core_resp_valid_o |-> !$isunknown(core_resp_rdata_o))
    else $error("l1_cache: responded with unknown data");

  a_one_response : assert property (@(posedge clk) disable iff (!rst_n)
    !(s1_resp && fill_now))
    else $error("l1_cache: a hit response and a fill response collided");

  a_no_array_conflict : assert property (@(posedge clk) disable iff (!rst_n)
    !(s2_wr_q && fill_now))
    else $error("l1_cache: store write and fill contended for the data array");

  // Two live MSHRs must never target the same way of the same set, or their
  // fills race and one line is lost.
  // At most one request may be in the replay loop at a time. If this fires,
  // a replaying request is about to overwrite another one already parked in
  // the slot, and that request's response is lost forever (bug B5).
  a_replay_slot_not_overwritten : assert property (@(posedge clk) disable iff (!rst_n)
    s1_replay |-> !rpl_valid_q)
    else $error("l1_cache: a replaying request would overwrite the occupied replay slot");

  a_no_way_double_booking : assert property (@(posedge clk) disable iff (!rst_n)
    s1_alloc |-> !set_reserved[s1_victim])
    else $error("l1_cache: allocated an MSHR onto way %0d of set %0d which another MSHR already reserved", s1_victim, s1_set);
`endif

endmodule : l1_cache
