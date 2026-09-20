//=============================================================================
// tbe_file.sv
//
// Transaction buffer entries at a directory bank. One entry per line the
// directory is currently waiting on.
//
// Interfaces: clk, rst_n; allocate, CAM lookup by line address, update, free,
// and a flat read-out of every entry.
//
// alloc_ready_o and the CAM outputs are COMBINATIONAL -- the directory decides
// and allocates in the same cycle.
//
// The one non-obvious thing: every entry carries an age, and an assertion here
// fires if any entry outlives TBE_TIMEOUT. That is the deadlock detector for
// the directory side, and it lives in the file that owns the state rather than
// in the testbench, so it is active in every simulation that instantiates a
// directory -- including ones nobody wrote a liveness test for.
//=============================================================================

module tbe_file
  import coh_pkg::*;
#(
  // The liveness bound. Defaults to the package value; a configuration with a
  // longer worst-case round trip -- the mesh, versus a direct connection --
  // raises it to a measured figure rather than an assumed one.
  parameter int unsigned TIMEOUT = TBE_TIMEOUT
) (
  input  logic                        clk,
  input  logic                        rst_n,

  input  logic                        alloc_valid_i,
  output logic                        alloc_ready_o,
  input  tbe_state_e                  alloc_state_i,
  input  logic [LINE_ADDR_W-1:0]      alloc_addr_i,
  input  logic [L2_WAY_W-1:0]         alloc_way_i,
  input  logic [TILE_ID_W-1:0]        alloc_requester_i,
  input  logic signed [ACK_CNT_W-1:0] alloc_ack_cnt_i,
  output logic [TBE_IDX_W-1:0]        alloc_idx_o,

  input  logic [LINE_ADDR_W-1:0]      lookup_addr_i,
  output logic                        lookup_hit_o,
  output logic [TBE_IDX_W-1:0]        lookup_idx_o,

  input  logic                        ack_dec_valid_i,
  input  logic [TBE_IDX_W-1:0]        ack_dec_idx_i,

  input  logic                        free_valid_i,
  input  logic [TBE_IDX_W-1:0]        free_idx_i,

  output tbe_e [TBE_ENTRIES-1:0]      entries_o
);

  tbe_e [TBE_ENTRIES-1:0] tbe_q;
  assign entries_o = tbe_q;

  logic [TBE_ENTRIES-1:0] free_mask;
  logic [TBE_ENTRIES-1:0] cam_hit;

  always_comb begin
    for (int unsigned i = 0; i < TBE_ENTRIES; i++) begin
      free_mask[i] = !tbe_q[i].valid;
      cam_hit[i]   = tbe_q[i].valid && (tbe_q[i].addr == lookup_addr_i);
    end
  end

  assign alloc_ready_o = |free_mask;
  assign lookup_hit_o  = |cam_hit;

  always_comb begin
    alloc_idx_o  = '0;
    lookup_idx_o = '0;
    for (int unsigned i = TBE_ENTRIES; i > 0; i--) begin
      if (free_mask[i - 1]) begin
        alloc_idx_o = TBE_IDX_W'(i - 1);
      end
      if (cam_hit[i - 1]) begin
        lookup_idx_o = TBE_IDX_W'(i - 1);
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int unsigned i = 0; i < TBE_ENTRIES; i++) begin
        tbe_q[i] <= '0;
      end
    end else begin
      for (int unsigned i = 0; i < TBE_ENTRIES; i++) begin
        if (tbe_q[i].valid) begin
          tbe_q[i].age <= tbe_q[i].age + TBE_AGE_W'(1);
        end
      end

      if (free_valid_i) begin
        tbe_q[free_idx_i].valid <= 1'b0;
        tbe_q[free_idx_i].state <= TBE_INVALID;
      end

      if (ack_dec_valid_i) begin
        tbe_q[ack_dec_idx_i].ack_cnt <= tbe_q[ack_dec_idx_i].ack_cnt - ACK_CNT_W'(1);
      end

      if (alloc_valid_i && alloc_ready_o) begin
        tbe_q[alloc_idx_o].valid     <= 1'b1;
        tbe_q[alloc_idx_o].state     <= alloc_state_i;
        tbe_q[alloc_idx_o].addr      <= alloc_addr_i;
        tbe_q[alloc_idx_o].way       <= alloc_way_i;
        tbe_q[alloc_idx_o].requester <= alloc_requester_i;
        tbe_q[alloc_idx_o].ack_cnt   <= alloc_ack_cnt_i;
        tbe_q[alloc_idx_o].age       <= '0;
      end
    end
  end

`ifndef SYNTHESIS
  for (genvar i = 0; i < int'(TBE_ENTRIES); i++) begin : gen_tbe_asserts
    // The directory-side deadlock detector. A real assertion, not a warning.
    a_tbe_liveness : assert property (@(posedge clk) disable iff (!rst_n)
      tbe_q[i].valid |-> (tbe_q[i].age < TBE_AGE_W'(TIMEOUT)))
      else $error("tbe_file: entry %0d for line %0h has been live %0d cycles, exceeding the liveness bound -- whatever it is waiting for is not coming", i, tbe_q[i].addr, tbe_q[i].age);
  end

  a_no_duplicate_address : assert property (@(posedge clk) disable iff (!rst_n)
    $onehot0(cam_hit))
    else $error("tbe_file: two entries share line address %0h", lookup_addr_i);

  a_free_live : assert property (@(posedge clk) disable iff (!rst_n)
    free_valid_i |-> tbe_q[free_idx_i].valid)
    else $error("tbe_file: freed entry %0d which was not allocated", free_idx_i);
`endif

endmodule : tbe_file
