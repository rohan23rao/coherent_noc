//=============================================================================
// skid_buffer.sv
//
// Two-entry ready/valid cut. Registers the forward path (valid, data) and the
// backward path (ready) so neither crosses the module boundary
// combinationally, while still sustaining one transfer per cycle.
//
// Interfaces: clk, rst_n; up_valid_i / up_ready_o / up_data_i and
// dn_valid_o / dn_ready_i / dn_data_o. All outputs registered.
//
// The one non-obvious thing: the second entry ("skid") exists only to absorb
// the flit that is already in flight when the downstream deasserts ready. A
// one-entry register slice cannot do this without either dropping that flit or
// making up_ready_o combinationally dependent on dn_ready_i -- which is the
// exact path the buffer was inserted to break.
//=============================================================================

module skid_buffer #(
  parameter int unsigned WIDTH = 8
) (
  input  logic              clk,
  input  logic              rst_n,

  input  logic              up_valid_i,
  output logic              up_ready_o,
  input  logic [WIDTH-1:0]  up_data_i,

  output logic              dn_valid_o,
  input  logic              dn_ready_i,
  output logic [WIDTH-1:0]  dn_data_o
);

  logic [WIDTH-1:0] data_q;
  logic             valid_q;
  logic [WIDTH-1:0] skid_data_q;
  logic             skid_valid_q;

  // Accept upstream whenever the skid slot is free.
  assign up_ready_o = !skid_valid_q;
  assign dn_valid_o = valid_q;
  assign dn_data_o  = data_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      data_q       <= '0;
      valid_q      <= 1'b0;
      skid_data_q  <= '0;
      skid_valid_q <= 1'b0;
    end else begin
      if (!valid_q || dn_ready_i) begin
        // Output slot is free or draining this cycle: refill it, preferring the
        // skid slot so ordering is preserved.
        if (skid_valid_q) begin
          data_q       <= skid_data_q;
          valid_q      <= 1'b1;
          skid_valid_q <= 1'b0;
          // Upstream was held off while the skid slot was full, so nothing can
          // be arriving this cycle to refill it.
        end else begin
          data_q  <= up_data_i;
          valid_q <= up_valid_i && up_ready_o;
        end
      end else if (up_valid_i && up_ready_o) begin
        // Output slot is occupied and stalled: park the arriving beat.
        skid_data_q  <= up_data_i;
        skid_valid_q <= 1'b1;
      end
    end
  end

`ifndef SYNTHESIS
  a_valid_stable : assert property (@(posedge clk) disable iff (!rst_n)
    (dn_valid_o && !dn_ready_i) |=> dn_valid_o)
    else $error("skid_buffer: valid dropped before ready");

  a_data_stable : assert property (@(posedge clk) disable iff (!rst_n)
    (dn_valid_o && !dn_ready_i) |=> $stable(dn_data_o))
    else $error("skid_buffer: payload changed while stalled");

  a_no_overflow : assert property (@(posedge clk) disable iff (!rst_n)
    !(up_valid_i && up_ready_o && skid_valid_q))
    else $error("skid_buffer: accepted a beat with the skid slot already full");
`endif

endmodule : skid_buffer
