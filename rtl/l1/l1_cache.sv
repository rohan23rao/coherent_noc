//=============================================================================
// l1_cache.sv
//
// Private L1 data cache: 64 sets x 2 ways x 32 B, physically indexed and
// tagged, write-back / write-allocate. At Phase 4 it is BLOCKING -- one miss at
// a time, the core interface stalls while it is serviced. The MSHR file and
// non-blocking behaviour arrive in Phase 5, coherence in Phase 6.
//
// Three stages:
//   S0  accept a core request, issue the data array read
//   S1  compare tags, resolve hit or miss, form the store-merged line
//   S2  write the data array (store hit)
//
// Interfaces: clk, rst_n; the core request/response channels; the line-granular
// memory channel; and a combinational debug bus exposing per-way state and tag
// for the coherence checker.
//
// Three non-obvious things.
//
// 1. Tags and coherence state live in FLOPS; only the data lives in sram_1rw.
//    That is a deliberate departure from "every array is an sram_1rw" and it is
//    argued in docs/decisions.md D10: from Phase 6 a snoop has to look up and
//    modify state on a line the pipeline may be using in the same cycle, and a
//    single-ported tag SRAM makes that a structural conflict on every snoop.
//    Real designs solve it with a duplicated snoop tag array; one flop copy is
//    the same trade with the area stated honestly.
//
// 2. The data array is single-ported, so a write in S2 and a read in S0 cannot
//    both happen. S0 is held whenever S2 has a write pending. Back-to-back
//    stores therefore issue one every two cycles while loads issue one per
//    cycle. That is the price of sram_1rw rather than a 1R1W macro.
//
// 3. A store in S2 and a load to the same line in S1 are a genuine RAW hazard:
//    the load's array read was issued before the store's write landed, so it
//    would return pre-store data. S1 forwards the S2 line rather than stalling.
//=============================================================================

module l1_cache
  import coh_pkg::*;
(
  input  logic                        clk,
  input  logic                        rst_n,

  // ---- Core request ----
  input  logic                        core_req_valid_i,
  output logic                        core_req_ready_o,
  input  core_op_e                    core_op_i,
  input  logic [ADDR_W-1:0]           core_addr_i,
  input  logic [WORD_W-1:0]           core_wdata_i,
  input  logic [BE_W-1:0]             core_be_i,
  input  logic [CORE_TAG_W-1:0]       core_tag_i,

  // ---- Core response ----
  output logic                        core_resp_valid_o,
  output logic [CORE_TAG_W-1:0]       core_resp_tag_o,
  output logic [WORD_W-1:0]           core_resp_rdata_o,

  // ---- Memory ----
  output logic                        mem_req_valid_o,
  input  logic                        mem_req_ready_i,
  output logic [LINE_ADDR_W-1:0]      mem_req_addr_o,
  output logic                        mem_req_we_o,
  output logic [LINE_W-1:0]           mem_req_wdata_o,
  input  logic                        mem_resp_valid_i,
  input  logic [LINE_W-1:0]           mem_resp_rdata_i,

  // ---- Debug bus (combinational, read-only) ----
  input  logic [L1_IDX_W-1:0]         dbg_set_i,
  input  logic [L1_WAY_W-1:0]         dbg_way_i,
  output l1_state_e                   dbg_state_o,
  output logic [L1_TAG_W-1:0]         dbg_tag_o
);

  //---------------------------------------------------------------------------
  // Arrays: tag and state in flops, data in SRAM.
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
  // Miss FSM
  //---------------------------------------------------------------------------
  typedef enum logic [2:0] {
    M_IDLE     = 3'd0,
    M_WB_REQ   = 3'd1,
    M_WB_WAIT  = 3'd2,
    M_FET_REQ  = 3'd3,
    M_FET_WAIT = 3'd4,
    M_FILL     = 3'd5
  } miss_state_e;

  miss_state_e            miss_q;
  logic [ADDR_W-1:0]      miss_addr_q;
  core_op_e               miss_op_q;
  logic [WORD_W-1:0]      miss_wdata_q;
  logic [BE_W-1:0]        miss_be_q;
  logic [CORE_TAG_W-1:0]  miss_tag_q;
  logic [L1_WAY_W-1:0]    miss_way_q;
  logic [LINE_W-1:0]      miss_line_q;
  logic [LINE_ADDR_W-1:0] miss_wb_addr_q;
  logic [LINE_W-1:0]      miss_wb_data_q;

  //---------------------------------------------------------------------------
  // Pipeline registers
  //---------------------------------------------------------------------------
  logic                   s1_valid_q;
  logic [ADDR_W-1:0]      s1_addr_q;
  core_op_e               s1_op_q;
  logic [WORD_W-1:0]      s1_wdata_q;
  logic [BE_W-1:0]        s1_be_q;
  logic [CORE_TAG_W-1:0]  s1_tag_q;

  logic                   s2_wr_q;
  logic [L1_IDX_W-1:0]    s2_set_q;
  logic [L1_WAY_W-1:0]    s2_way_q;
  logic [LINE_W-1:0]      s2_line_q;

  //---------------------------------------------------------------------------
  // S1 combinational
  //---------------------------------------------------------------------------
  logic [L1_IDX_W-1:0]    s1_set;
  logic [L1_TAG_W-1:0]    s1_tag;
  logic [WORD_SEL_W-1:0]  s1_word;
  logic [L1_WAYS-1:0]     s1_hit_way;
  logic                   s1_hit;
  logic [L1_WAY_W-1:0]    s1_hit_idx;
  logic [LINE_W-1:0]      s1_raw_line;
  logic [LINE_W-1:0]      s1_hit_line;
  logic                   s1_forward;
  logic [LINE_W-1:0]      s1_merged;
  logic [WORD_W-1:0]      s1_rword;
  logic [L1_WAY_W-1:0]    s1_victim;

  assign s1_set    = addr_l1_index(s1_addr_q);
  assign s1_tag    = addr_l1_tag(s1_addr_q);
  assign s1_word   = addr_word_sel(s1_addr_q);
  assign s1_victim = L1_WAY_W'(plru_q[s1_set]);

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

  assign s1_raw_line = data_rdata[s1_hit_idx];

  // RAW forward: the S2 write has not reached the array yet, so a read issued
  // in S0 sees pre-store data. See header note 3.
  assign s1_forward  = s2_wr_q && (s2_set_q == s1_set) && (s2_way_q == s1_hit_idx);
  assign s1_hit_line = s1_forward ? s2_line_q : s1_raw_line;

  always_comb begin
    s1_merged = s1_hit_line;
    for (int unsigned b = 0; b < BE_W; b++) begin
      if (s1_be_q[b]) begin
        s1_merged[s1_word * WORD_W + b * 8 +: 8] = s1_wdata_q[b * 8 +: 8];
      end
    end
  end

  // The word returned to the core. For a load it is the resident word; for a
  // store it is the word *after* the store has been applied. Defining it that
  // way makes a store's completion directly checkable by the scoreboard, and
  // it matches what the fill path already returns on a store miss -- the two
  // disagreeing was bug B4.
  assign s1_rword = (s1_op_q == OP_ST)
                    ? s1_merged  [s1_word * WORD_W +: WORD_W]
                    : s1_hit_line[s1_word * WORD_W +: WORD_W];

  //---------------------------------------------------------------------------
  // Fill line: the pending store is merged in before the line is written, so
  // the array and the word returned to the core agree on a store miss.
  //---------------------------------------------------------------------------
  logic [WORD_SEL_W-1:0] miss_word;
  logic [L1_IDX_W-1:0]   miss_set;
  logic [LINE_W-1:0]     miss_fill_line;

  assign miss_word = addr_word_sel(miss_addr_q);
  assign miss_set  = addr_l1_index(miss_addr_q);

  always_comb begin
    miss_fill_line = miss_line_q;
    if (miss_op_q == OP_ST) begin
      for (int unsigned b = 0; b < BE_W; b++) begin
        if (miss_be_q[b]) begin
          miss_fill_line[miss_word * WORD_W + b * 8 +: 8] = miss_wdata_q[b * 8 +: 8];
        end
      end
    end
  end

  //---------------------------------------------------------------------------
  // Control
  //---------------------------------------------------------------------------
  logic stall;
  logic s1_fire_miss;
  logic accept;
  logic fill_now;

  assign stall        = (miss_q != M_IDLE);
  assign s1_fire_miss = s1_valid_q && !s1_hit && !stall;
  assign fill_now     = (miss_q == M_FILL);
  assign core_req_ready_o = !stall && !s1_fire_miss && !s2_wr_q;
  assign accept           = core_req_valid_i && core_req_ready_o;

  //---------------------------------------------------------------------------
  // Data array port arbitration. Priority: fill, then S2 store write, then a
  // new lookup. Only one may use the single port in a cycle.
  //---------------------------------------------------------------------------
  always_comb begin
    data_addr  = addr_l1_index(core_addr_i);
    data_wdata = '0;
    for (int unsigned w = 0; w < L1_WAYS; w++) begin
      data_en[w] = 1'b0;
      data_we[w] = 1'b0;
    end

    if (fill_now) begin
      data_addr             = miss_set;
      data_wdata            = miss_fill_line;
      data_en[miss_way_q]   = 1'b1;
      data_we[miss_way_q]   = 1'b1;
    end else if (s2_wr_q) begin
      data_addr             = s2_set_q;
      data_wdata            = s2_line_q;
      data_en[s2_way_q]     = 1'b1;
      data_we[s2_way_q]     = 1'b1;
    end else if (accept) begin
      data_addr = addr_l1_index(core_addr_i);
      for (int unsigned w = 0; w < L1_WAYS; w++) begin
        data_en[w] = 1'b1;
      end
    end
  end

  //---------------------------------------------------------------------------
  // Memory request
  //---------------------------------------------------------------------------
  always_comb begin
    mem_req_valid_o = 1'b0;
    mem_req_addr_o  = '0;
    mem_req_we_o    = 1'b0;
    mem_req_wdata_o = '0;
    unique case (miss_q)
      M_WB_REQ: begin
        mem_req_valid_o = 1'b1;
        mem_req_addr_o  = miss_wb_addr_q;
        mem_req_we_o    = 1'b1;
        mem_req_wdata_o = miss_wb_data_q;
      end
      M_FET_REQ: begin
        mem_req_valid_o = 1'b1;
        mem_req_addr_o  = addr_line(miss_addr_q);
      end
      M_IDLE, M_WB_WAIT, M_FET_WAIT, M_FILL: begin
        mem_req_valid_o = 1'b0;
      end
      default: begin
        mem_req_valid_o = 1'b0;
        $error("l1_cache: illegal miss state %0d", miss_q);
      end
    endcase
  end

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
      s2_wr_q           <= 1'b0;
      s2_set_q          <= '0;
      s2_way_q          <= '0;
      s2_line_q         <= '0;
      core_resp_valid_o <= 1'b0;
      core_resp_tag_o   <= '0;
      core_resp_rdata_o <= '0;
      miss_q            <= M_IDLE;
      miss_addr_q       <= '0;
      miss_op_q         <= OP_LD;
      miss_wdata_q      <= '0;
      miss_be_q         <= '0;
      miss_tag_q        <= '0;
      miss_way_q        <= '0;
      miss_line_q       <= '0;
      miss_wb_addr_q    <= '0;
      miss_wb_data_q    <= '0;
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

      // ---- S1: resolve the request already in the stage ----
      if (s1_valid_q && !stall) begin
        if (s1_hit) begin
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
        end else begin
          miss_addr_q  <= s1_addr_q;
          miss_op_q    <= s1_op_q;
          miss_wdata_q <= s1_wdata_q;
          miss_be_q    <= s1_be_q;
          miss_tag_q   <= s1_tag_q;
          miss_way_q   <= s1_victim;
          if (state_q[s1_set][s1_victim] == L1_M) begin
            miss_wb_addr_q <= l1_line_addr(tag_q[s1_set][s1_victim], s1_set);
            miss_wb_data_q <= data_rdata[s1_victim];
            miss_q         <= M_WB_REQ;
          end else begin
            miss_q <= M_FET_REQ;
          end
        end
      end

      // ---- S0 -> S1. Written after S1 so a newly accepted request is not
      //      clobbered by the resolution of the one leaving the stage. ----
      if (!stall) begin
        s1_valid_q <= accept;
        if (accept) begin
          s1_addr_q  <= core_addr_i;
          s1_op_q    <= core_op_i;
          s1_wdata_q <= core_wdata_i;
          s1_be_q    <= core_be_i;
          s1_tag_q   <= core_tag_i;
        end
      end

      // ---- Miss FSM ----
      unique case (miss_q)
        M_IDLE: begin
          // Entry is handled in the S1 block above.
        end
        M_WB_REQ: begin
          if (mem_req_ready_i) begin
            miss_q <= M_WB_WAIT;
          end
        end
        M_WB_WAIT: begin
          if (mem_resp_valid_i) begin
            miss_q <= M_FET_REQ;
          end
        end
        M_FET_REQ: begin
          if (mem_req_ready_i) begin
            miss_q <= M_FET_WAIT;
          end
        end
        M_FET_WAIT: begin
          if (mem_resp_valid_i) begin
            miss_line_q <= mem_resp_rdata_i;
            miss_q      <= M_FILL;
          end
        end
        M_FILL: begin
          state_q[miss_set][miss_way_q] <= (miss_op_q == OP_ST) ? L1_M : L1_E;
          tag_q[miss_set][miss_way_q]   <= addr_l1_tag(miss_addr_q);
          plru_q[miss_set]              <= ~miss_way_q[0];
          core_resp_valid_o             <= 1'b1;
          core_resp_tag_o               <= miss_tag_q;
          core_resp_rdata_o             <= miss_fill_line[miss_word * WORD_W +: WORD_W];
          miss_q                        <= M_IDLE;
        end
        default: begin
          miss_q <= M_IDLE;
          $error("l1_cache: illegal miss state %0d", miss_q);
        end
      endcase
    end
  end

  //---------------------------------------------------------------------------
  // Debug bus
  //---------------------------------------------------------------------------
  assign dbg_state_o = state_q[dbg_set_i][dbg_way_i];
  assign dbg_tag_o   = tag_q[dbg_set_i][dbg_way_i];

`ifndef SYNTHESIS
  a_one_hit_way : assert property (@(posedge clk) disable iff (!rst_n)
    s1_valid_q |-> $onehot0(s1_hit_way))
    else $error("l1_cache: the same line is resident in two ways at set %0d", s1_set);

  a_no_req_while_stalled : assert property (@(posedge clk) disable iff (!rst_n)
    (miss_q != M_IDLE) |-> !core_req_ready_o)
    else $error("l1_cache: accepted a request while a miss was outstanding");

  a_resp_known : assert property (@(posedge clk) disable iff (!rst_n)
    core_resp_valid_o |-> !$isunknown(core_resp_rdata_o))
    else $error("l1_cache: responded with unknown data");

  a_no_data_port_conflict : assert property (@(posedge clk) disable iff (!rst_n)
    !(fill_now && s2_wr_q))
    else $error("l1_cache: fill and store-hit write contended for the data array");
`endif

endmodule : l1_cache
