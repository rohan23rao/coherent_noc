//=============================================================================
// mshr_file.sv
//
// Miss-status holding registers: MSHR_ENTRIES entries, fully associative on the
// line address.
//
// Interfaces: clk, rst_n; an allocate port, a CAM lookup port, an update port
// (state, ack count, fill data), a free port, and a flat read-out of every
// entry for the cache controller to arbitrate over.
//
// alloc_ready_o, the CAM outputs and entries_o are COMBINATIONAL: allocation
// and lookup both happen inside the cache's S1 stage.
//
// The one non-obvious thing: there is exactly ONE MSHR per line address, and a
// second request to a line that already has one STALLS rather than merging into
// it. Merging is the standard optimization and it is deliberately not done
// here -- see docs/decisions.md D11. The CAM is what enforces it, and an
// assertion checks that two valid entries never share an address, because a
// duplicate MSHR means two fills racing to write the same way.
//=============================================================================

module mshr_file
  import coh_pkg::*;
#(
  // The liveness bound, as a parameter rather than a constant: raising it is
  // how a suspected deadlock is told apart from a slow path. If the run
  // completes with the bound at ten times the default, it was latency; if it
  // still fires, it was a deadlock. That measurement is what settled B14.
  parameter int unsigned TIMEOUT = MSHR_TIMEOUT
) (
  input  logic                          clk,
  input  logic                          rst_n,

  // ---- Allocate ----
  input  logic                          alloc_valid_i,
  output logic                          alloc_ready_o,
  input  logic [LINE_ADDR_W-1:0]        alloc_addr_i,
  input  l1_state_e                     alloc_state_i,
  input  core_op_e                      alloc_op_i,
  input  logic [WORD_W-1:0]             alloc_wdata_i,
  input  logic [BE_W-1:0]               alloc_be_i,
  input  logic [WORD_SEL_W-1:0]         alloc_word_sel_i,
  input  logic [CORE_TAG_W-1:0]         alloc_tag_i,
  input  logic [L1_WAY_W-1:0]           alloc_way_i,
  input  logic                          alloc_needs_wb_i,
  input  logic                          alloc_is_evict_i,
  // The victim's dirty line, parked in the entry's data field until the
  // writeback completes. See l1_cache header note 2.
  input  logic [LINE_W-1:0]             alloc_wb_data_i,
  input  logic [LINE_ADDR_W-1:0]        alloc_wb_addr_i,
  output logic [MSHR_IDX_W-1:0]         alloc_idx_o,

  // ---- CAM lookup ----
  input  logic [LINE_ADDR_W-1:0]        lookup_addr_i,
  output logic                          lookup_hit_o,
  output logic [MSHR_IDX_W-1:0]         lookup_idx_o,

  // ---- Update. One per cycle: the controller arbitrates between the VN1 and
  //      VN2 handlers before driving this port. ----
  input  logic                          upd_valid_i,
  input  logic [MSHR_IDX_W-1:0]         upd_idx_i,
  input  logic                          upd_set_state_i,
  input  l1_state_e                     upd_state_i,
  input  logic                          upd_set_data_i,
  input  logic [LINE_W-1:0]             upd_data_i,
  input  logic                          upd_ack_dec_i,
  input  logic                          upd_ack_add_i,
  input  logic                          upd_done_i,
  input  logic signed [ACK_CNT_W-1:0]   upd_ack_val_i,

  // ---- Free ----
  input  logic                          free_valid_i,
  input  logic [MSHR_IDX_W-1:0]         free_idx_i,

  // ---- Read-out ----
  output mshr_e [MSHR_ENTRIES-1:0]      entries_o
);

  mshr_e [MSHR_ENTRIES-1:0] mshr_q;

  assign entries_o = mshr_q;

  //---------------------------------------------------------------------------
  // Free-entry selection and CAM
  //---------------------------------------------------------------------------
  logic [MSHR_ENTRIES-1:0] free_mask;
  logic [MSHR_ENTRIES-1:0] cam_hit;

  always_comb begin
    for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
      free_mask[i] = !mshr_q[i].valid;
      cam_hit[i]   = mshr_q[i].valid && (mshr_q[i].addr == lookup_addr_i);
    end
  end

  assign alloc_ready_o = |free_mask;
  assign lookup_hit_o  = |cam_hit;

  always_comb begin
    alloc_idx_o = '0;
    for (int unsigned i = MSHR_ENTRIES; i > 0; i--) begin
      if (free_mask[i - 1]) begin
        alloc_idx_o = MSHR_IDX_W'(i - 1);
      end
    end

    lookup_idx_o = '0;
    for (int unsigned i = MSHR_ENTRIES; i > 0; i--) begin
      if (cam_hit[i - 1]) begin
        lookup_idx_o = MSHR_IDX_W'(i - 1);
      end
    end
  end

  //---------------------------------------------------------------------------
  // Sequential
  //---------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
        mshr_q[i] <= '0;
      end
    end else begin
      if (free_valid_i) begin
        mshr_q[free_idx_i].valid      <= 1'b0;
        mshr_q[free_idx_i].data_valid <= 1'b0;
        mshr_q[free_idx_i].state      <= L1_I;
        mshr_q[free_idx_i].done       <= 1'b0;
      end

      if (alloc_valid_i && alloc_ready_o) begin
        mshr_q[alloc_idx_o].valid      <= 1'b1;
        mshr_q[alloc_idx_o].addr       <= alloc_addr_i;
        mshr_q[alloc_idx_o].state      <= alloc_state_i;
        mshr_q[alloc_idx_o].ack_cnt    <= '0;
        mshr_q[alloc_idx_o].data       <= alloc_wb_data_i;
        mshr_q[alloc_idx_o].data_valid <= 1'b0;
        mshr_q[alloc_idx_o].op         <= alloc_op_i;
        mshr_q[alloc_idx_o].wdata      <= alloc_wdata_i;
        mshr_q[alloc_idx_o].be         <= alloc_be_i;
        mshr_q[alloc_idx_o].word_sel   <= alloc_word_sel_i;
        mshr_q[alloc_idx_o].core_tag   <= alloc_tag_i;
        mshr_q[alloc_idx_o].victim_way <= alloc_way_i;
        mshr_q[alloc_idx_o].needs_wb   <= alloc_needs_wb_i;
        mshr_q[alloc_idx_o].wb_addr    <= alloc_wb_addr_i;
        mshr_q[alloc_idx_o].is_evict   <= alloc_is_evict_i;
        mshr_q[alloc_idx_o].done       <= 1'b0;
        mshr_q[alloc_idx_o].fwd_pend   <= '0;
      end

      if (upd_valid_i) begin
        if (upd_set_state_i) begin
          mshr_q[upd_idx_i].state <= upd_state_i;
        end
        if (upd_done_i) begin
          mshr_q[upd_idx_i].done <= 1'b1;
        end
        if (upd_set_data_i) begin
          mshr_q[upd_idx_i].data       <= upd_data_i;
          mshr_q[upd_idx_i].data_valid <= 1'b1;
        end
        // ack_cnt is SIGNED. A decrement may take it negative when Inv-Acks
        // overtake the Data that carries the AckCount; the later add credits
        // it back up. An unsigned counter wraps here and the entry never
        // completes.
        if (upd_ack_dec_i && upd_ack_add_i) begin
          mshr_q[upd_idx_i].ack_cnt <= mshr_q[upd_idx_i].ack_cnt + upd_ack_val_i
                                       - ACK_CNT_W'(1);
        end else if (upd_ack_dec_i) begin
          mshr_q[upd_idx_i].ack_cnt <= mshr_q[upd_idx_i].ack_cnt - ACK_CNT_W'(1);
        end else if (upd_ack_add_i) begin
          mshr_q[upd_idx_i].ack_cnt <= mshr_q[upd_idx_i].ack_cnt + upd_ack_val_i;
        end
      end
    end
  end

`ifndef SYNTHESIS
  // Verification-only state: how long each entry has been live. The cache
  // never reads it, so it is not part of mshr_e.
  logic [AGE_W-1:0] age_q [MSHR_ENTRIES];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
        age_q[i] <= '0;
      end
    end else begin
      for (int unsigned i = 0; i < MSHR_ENTRIES; i++) begin
        age_q[i] <= mshr_q[i].valid ? (age_q[i] + AGE_W'(1)) : '0;
      end
      if (alloc_valid_i && alloc_ready_o) begin
        age_q[alloc_idx_o] <= '0;
      end
    end
  end

  for (genvar i = 0; i < int'(MSHR_ENTRIES); i++) begin : gen_mshr_asserts
    // The cache-side deadlock detector, and the counterpart of the TBE bound
    // at the directory. Between them they cover both ends of every
    // transaction: nothing can be stuck without one of them firing.
    //
    // A deadlock does not produce a wrong value, it produces silence, so the
    // detector has to be an assertion that fires inside the design rather
    // than a testbench giving up after N cycles. A testbench watchdog says
    // "something did not finish"; this says which entry, on which line, in
    // which cache, on the cycle the bound was crossed.
    a_mshr_liveness : assert property (@(posedge clk) disable iff (!rst_n)
      mshr_q[i].valid |-> (age_q[i] < AGE_W'(TIMEOUT)))
      else $error("mshr_file: entry %0d for line %0h has been live %0d cycles, exceeding the liveness bound -- whatever it is waiting for is not coming", i, mshr_q[i].addr, age_q[i]);
  end

  // Two live MSHRs on one line means two fills racing to write the same way.
  a_no_duplicate_address : assert property (@(posedge clk) disable iff (!rst_n)
    $onehot0(cam_hit))
    else $error("mshr_file: two valid entries share line address %0h", lookup_addr_i);

  a_alloc_into_free : assert property (@(posedge clk) disable iff (!rst_n)
    (alloc_valid_i && alloc_ready_o) |-> !mshr_q[alloc_idx_o].valid)
    else $error("mshr_file: allocated over a live entry %0d", alloc_idx_o);

  a_free_live : assert property (@(posedge clk) disable iff (!rst_n)
    free_valid_i |-> mshr_q[free_idx_i].valid)
    else $error("mshr_file: freed entry %0d which was not allocated", free_idx_i);

  a_no_alloc_on_cam_hit : assert property (@(posedge clk) disable iff (!rst_n)
    (alloc_valid_i && alloc_ready_o && (lookup_addr_i == alloc_addr_i))
      |-> !lookup_hit_o)
    else $error("mshr_file: allocated a second MSHR for line %0h", alloc_addr_i);
`endif

endmodule : mshr_file
