//=============================================================================
// l2_bank.sv
//
// Storage for one L2 / directory bank: per-way metadata and data arrays, plus
// the lookup and update ports the directory controller drives. The directory
// metadata lives beside the L2 tag, which is the whole point of an inclusive
// L2 -- no separate directory storage, and no directory entry can exist for a
// line the L2 does not hold.
//
// Interfaces: clk, rst_n; a lookup port (present a set, get every way's
// metadata one cycle later), a data read port, and a write port for metadata
// and data.
//
// This bank is the home for the quarter of the address space whose
// addr[6:5] selects it, so the bank bits are constant within the instance and
// are NOT stored in the tag (decision D4).
//
// The one non-obvious thing: metadata is read for ALL ways every lookup, and
// written for one. That is a genuine 8-way parallel read, which is what makes
// the single-cycle hit/miss decision possible, and it is why each way gets its
// own sram_1rw instance rather than one wide array -- a single wide array
// would have to be read and rewritten in full for every sharer-vector update.
//=============================================================================

module l2_bank
  import coh_pkg::*;
(
  input  logic                        clk,
  input  logic                        rst_n,

  // ---- Lookup: present a set, metadata for every way arrives next cycle ----
  input  logic                        lookup_en_i,
  input  logic [L2_IDX_W-1:0]         lookup_set_i,

  // ---- Write: one way's metadata and/or data ----
  input  logic                        wr_meta_en_i,
  input  logic [L2_IDX_W-1:0]         wr_set_i,
  input  logic [L2_WAY_W-1:0]         wr_way_i,
  input  dir_meta_t                   wr_meta_i,

  input  logic                        wr_data_en_i,
  input  logic [LINE_W-1:0]           wr_data_i,

  // ---- Read-out ----
  output dir_meta_t [L2_WAYS-1:0]     meta_o,
  output logic [LINE_W-1:0]           data_o [L2_WAYS],

  // ---- Debug read: combinational, independent of the lookup port ----
  input  logic [L2_IDX_W-1:0]         dbg_set_i,
  input  logic [L2_WAY_W-1:0]         dbg_way_i,
  output dir_meta_t                   dbg_meta_o
);


  // Metadata is small and is read every cycle by the controller's decision
  // logic, so it lives in flops; only the line data goes to SRAM. Same argument
  // as decision D10 for the L1: a single-ported metadata array would put the
  // sharer-vector update in contention with the next lookup on every request.
  dir_meta_t meta_q [L2_SETS][L2_WAYS];

  logic [L2_IDX_W-1:0] data_addr;
  logic                data_en   [L2_WAYS];
  logic                data_we   [L2_WAYS];

  assign data_addr = wr_data_en_i ? wr_set_i : lookup_set_i;

  always_comb begin
    for (int unsigned w = 0; w < L2_WAYS; w++) begin
      data_en[w] = wr_data_en_i ? (wr_way_i == L2_WAY_W'(w)) : lookup_en_i;
      data_we[w] = wr_data_en_i && (wr_way_i == L2_WAY_W'(w));
    end
  end

  for (genvar w = 0; w < int'(L2_WAYS); w++) begin : gen_way
    sram_1rw #(
      .WIDTH (LINE_W),
      .DEPTH (L2_SETS)
    ) u_data (
      .clk     (clk),
      .en_i    (data_en[w]),
      .we_i    (data_we[w]),
      .addr_i  (data_addr),
      .wdata_i (wr_data_i),
      .rdata_o (data_o[w])
    );
  end

  // Metadata read-out follows the set presented last cycle, so that it lines up
  // with the data array's one-cycle read latency.
  logic [L2_IDX_W-1:0] meta_set_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      meta_set_q <= '0;
      for (int unsigned s = 0; s < L2_SETS; s++) begin
        for (int unsigned w = 0; w < L2_WAYS; w++) begin
          meta_q[s][w] <= '0;
        end
      end
    end else begin
      if (lookup_en_i) begin
        meta_set_q <= lookup_set_i;
      end
      if (wr_meta_en_i) begin
        meta_q[wr_set_i][wr_way_i] <= wr_meta_i;
      end
    end
  end

  always_comb begin
    for (int unsigned w = 0; w < L2_WAYS; w++) begin
      meta_o[w] = meta_q[meta_set_q][w];
    end
  end

  assign dbg_meta_o = meta_q[dbg_set_i][dbg_way_i];

`ifndef SYNTHESIS
  a_no_meta_data_conflict : assert property (@(posedge clk) disable iff (!rst_n)
    !(wr_data_en_i && lookup_en_i))
    else $error("l2_bank: a data write and a lookup contended for the array port");

  // Two ways of a set must never hold the same tag.
  for (genvar w0 = 0; w0 < int'(L2_WAYS); w0++) begin : gen_dup_outer
    for (genvar w1 = w0 + 1; w1 < int'(L2_WAYS); w1++) begin : gen_dup_inner
      a_no_duplicate_tag : assert property (@(posedge clk) disable iff (!rst_n)
        !(meta_q[meta_set_q][w0].valid && meta_q[meta_set_q][w1].valid &&
          (meta_q[meta_set_q][w0].tag == meta_q[meta_set_q][w1].tag)))
        else $error("l2_bank: set %0d holds the same tag in ways %0d and %0d", meta_set_q, w0, w1);
    end
  end
`endif

endmodule : l2_bank
