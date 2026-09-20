//=============================================================================
// fifo.sv
//
// Synchronous FIFO with ready/valid on both sides. Used for VC buffers and for
// every message queue in the tile.
//
// Interfaces: clk, rst_n; wr_valid_i / wr_ready_o / wr_data_i,
// rd_valid_o / rd_ready_i / rd_data_o; count_o and the two flags.
//
// rd_data_o is COMBINATIONAL out of the storage array (a show-ahead / first-
// word-fall-through FIFO): the head is visible the cycle it is written, not the
// cycle after. Declared here because the registered-output rule would otherwise
// add a cycle to every hop in the network.
//
// The one non-obvious thing: wr_ready_o does not depend on rd_ready_i, and
// rd_valid_o does not depend on wr_valid_i. Those two independences are what
// make the FIFO a legal ready/valid cut in one direction; a FIFO whose full
// signal is a function of the downstream ready reintroduces the combinational
// loop it was inserted to break.
//=============================================================================

module fifo #(
  parameter  int unsigned WIDTH = 8,
  parameter  int unsigned DEPTH = 4,
  localparam int unsigned CNT_W = $clog2(DEPTH + 1),
  localparam int unsigned PTR_W = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic                clk,
  input  logic                rst_n,

  input  logic                wr_valid_i,
  output logic                wr_ready_o,
  input  logic [WIDTH-1:0]    wr_data_i,

  output logic                rd_valid_o,
  input  logic                rd_ready_i,
  output logic [WIDTH-1:0]    rd_data_o,

  output logic [CNT_W-1:0]    count_o,
  output logic                empty_o,
  output logic                full_o
);

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [PTR_W-1:0] wr_ptr_q;
  logic [PTR_W-1:0] rd_ptr_q;
  logic [CNT_W-1:0] count_q;

  logic do_wr;
  logic do_rd;

  assign empty_o    = (count_q == CNT_W'(0));
  assign full_o     = (count_q == CNT_W'(DEPTH));
  assign wr_ready_o = !full_o;
  assign rd_valid_o = !empty_o;
  assign count_o    = count_q;

  assign do_wr = wr_valid_i && wr_ready_o;
  assign do_rd = rd_valid_o && rd_ready_i;

  assign rd_data_o = mem_q[rd_ptr_q];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr_q <= '0;
      rd_ptr_q <= '0;
      count_q  <= '0;
      for (int unsigned i = 0; i < DEPTH; i++) begin
        mem_q[i] <= '0;
      end
    end else begin
      if (do_wr) begin
        mem_q[wr_ptr_q] <= wr_data_i;
        wr_ptr_q <= (wr_ptr_q == PTR_W'(DEPTH - 1)) ? '0 : (wr_ptr_q + PTR_W'(1));
      end
      if (do_rd) begin
        rd_ptr_q <= (rd_ptr_q == PTR_W'(DEPTH - 1)) ? '0 : (rd_ptr_q + PTR_W'(1));
      end
      unique case ({do_wr, do_rd})
        2'b10:   count_q <= count_q + CNT_W'(1);
        2'b01:   count_q <= count_q - CNT_W'(1);
        default: count_q <= count_q;
      endcase
    end
  end

`ifndef SYNTHESIS
  a_no_wr_when_full : assert property (@(posedge clk) disable iff (!rst_n)
    !(wr_valid_i && full_o && !do_rd && wr_ready_o))
    else $error("fifo: write accepted while full");

  a_no_rd_when_empty : assert property (@(posedge clk) disable iff (!rst_n)
    !(rd_ready_i && empty_o && rd_valid_o))
    else $error("fifo: read accepted while empty");

  a_count_bound : assert property (@(posedge clk) disable iff (!rst_n)
    count_q <= CNT_W'(DEPTH))
    else $error("fifo: count exceeded DEPTH");
`endif

endmodule : fifo
