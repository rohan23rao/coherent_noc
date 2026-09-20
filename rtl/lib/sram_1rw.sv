//=============================================================================
// sram_1rw.sv
//
// Behavioral single-port (1RW) SRAM wrapper. Every array in the design -- L1
// tags, L1 data, L2 tags, L2 data -- instantiates this rather than declaring a
// bare `logic [W-1:0] mem [D]`, so that the port contract is explicit and the
// pipelines are designed against it.
//
// Interfaces: clk; en_i, we_i, addr_i, wdata_i, rdata_o.
//
// Port contract, which the pipelines must honour:
//   * ONE port. A read and a write cannot occur in the same cycle. The
//     structural hazard this creates is real and must be arbitrated by the
//     surrounding controller, not hidden here.
//   * Read latency is exactly ONE cycle: rdata_o is valid the cycle after
//     en_i && !we_i.
//   * No read-during-write forwarding. On a single-port array a same-cycle
//     read and write cannot both be requested, so the case does not arise --
//     but rdata_o holds its previous value across a write cycle rather than
//     showing the written data, and no consumer may depend on it.
//
// The one non-obvious thing: this is deliberately NOT a two-port array. A 2RW
// or 1R1W macro is substantially larger per bit, and forcing the controllers to
// arbitrate a single port here is what keeps the eventual area story honest.
//=============================================================================

module sram_1rw #(
  parameter  int unsigned WIDTH = 32,
  parameter  int unsigned DEPTH = 64,
  localparam int unsigned ADDR_BITS = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic                    clk,
  input  logic                    en_i,
  input  logic                    we_i,
  input  logic [ADDR_BITS-1:0]    addr_i,
  input  logic [WIDTH-1:0]        wdata_i,
  output logic [WIDTH-1:0]        rdata_o
);

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [WIDTH-1:0] rdata_q;

  always_ff @(posedge clk) begin // no-reset: SRAM contents are undefined until written, and a real macro has no reset port
    if (en_i) begin
      if (we_i) begin
        mem_q[addr_i] <= wdata_i;
      end else begin
        rdata_q <= mem_q[addr_i];
      end
    end
  end

  assign rdata_o = rdata_q;

endmodule : sram_1rw
