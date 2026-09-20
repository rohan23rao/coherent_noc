//=============================================================================
// mem_model.sv
//
// Behavioral main memory: line-granular, one outstanding request, fixed
// programmable latency.
//
// Interfaces: clk, rst_n; a request channel (valid/ready, line address, we,
// write data) and a response channel (valid, read data). All outputs
// registered.
//
// The one non-obvious thing: this models MEM_LINES lines and indexes with the
// low bits of the line address, so addresses further apart than that alias onto
// each other. That is fine because every test drives a bounded footprint, but
// it is a real limitation and the alternative -- a sparse associative model --
// would not be synthesizable-shaped and would hide capacity effects the
// directory phases need to see. An assertion flags any access outside the
// modelled window so the aliasing can never be silent.
//=============================================================================

module mem_model
  import coh_pkg::*;
#(
  parameter  int unsigned MEM_LINES = 1024,
  parameter  int unsigned LATENCY   = MEM_LATENCY,
  localparam int unsigned MEM_IDX_W = $clog2(MEM_LINES)
) (
  input  logic                        clk,
  input  logic                        rst_n,

  input  logic                        req_valid_i,
  output logic                        req_ready_o,
  input  logic [LINE_ADDR_W-1:0]      req_addr_i,
  input  logic                        req_we_i,
  input  logic [LINE_W-1:0]           req_wdata_i,

  output logic                        resp_valid_o,
  output logic [LINE_W-1:0]           resp_rdata_o
);

  localparam int unsigned CNT_W = (LATENCY > 1) ? $clog2(LATENCY + 1) : 1;

  logic [LINE_W-1:0]      mem_q [MEM_LINES];

  logic                   busy_q;
  logic [CNT_W-1:0]       count_q;
  logic [LINE_W-1:0]      hold_q;

  logic [MEM_IDX_W-1:0]   idx;
  assign idx = req_addr_i[MEM_IDX_W-1:0];

  assign req_ready_o = !busy_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      busy_q       <= 1'b0;
      count_q      <= '0;
      hold_q       <= '0;
      resp_valid_o <= 1'b0;
      resp_rdata_o <= '0;
      for (int unsigned i = 0; i < MEM_LINES; i++) begin
        mem_q[i] <= '0;
      end
    end else begin
      resp_valid_o <= 1'b0;

      if (!busy_q) begin
        if (req_valid_i) begin
          if (req_we_i) begin
            mem_q[idx] <= req_wdata_i;
            hold_q     <= req_wdata_i;
          end else begin
            hold_q <= mem_q[idx];
          end
          busy_q  <= 1'b1;
          count_q <= CNT_W'(LATENCY);
        end
      end else begin
        if (count_q > CNT_W'(1)) begin
          count_q <= count_q - CNT_W'(1);
        end else begin
          busy_q       <= 1'b0;
          count_q      <= '0;
          resp_valid_o <= 1'b1;
          resp_rdata_o <= hold_q;
        end
      end
    end
  end

`ifndef SYNTHESIS
  a_in_window : assert property (@(posedge clk) disable iff (!rst_n)
    (req_valid_i && req_ready_o)
      |-> (req_addr_i < LINE_ADDR_W'(MEM_LINES)))
    else $error("mem_model: line address %0d is outside the %0d-line modelled window and would alias", req_addr_i, MEM_LINES);

  a_no_req_when_busy : assert property (@(posedge clk) disable iff (!rst_n)
    (req_valid_i && busy_q) |-> !req_ready_o)
    else $error("mem_model: accepted a request while busy");
`endif

endmodule : mem_model
