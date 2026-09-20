//=============================================================================
// route_compute.sv
//
// Dimension-ordered (XY) route computation. Runs on the head flit only, at
// buffer-write time, and the result is latched into the input VC's state.
//
// Interfaces: dst_x_i, dst_y_i in; out_port_o out. Purely COMBINATIONAL -- it
// sits inside the buffer-write stage and its result is registered by the
// input unit, so registering it here would add a pipeline stage to every hop.
//
// Coordinate convention: tile n sits at (x,y) = (n[0], n[1]); y increases
// southward, matching the system diagram where tile 0 is above tile 2.
//
// The one non-obvious thing: XY is deadlock-free by construction, and the
// reason is the turn model, not the buffering. Routing all of X before any of
// Y forbids two of the four turns (north-to-east and south-to-east are never
// taken, in this orientation), which makes the channel dependency graph
// acyclic. No amount of buffering would save a routing function that permitted
// a cycle, and no VC is needed to make this one safe. See docs/deadlock.md.
//=============================================================================

module route_compute
  import coh_pkg::*;
#(
  parameter int unsigned MY_X = 0,
  parameter int unsigned MY_Y = 0
) (
  input  logic [MESH_X_W-1:0] dst_x_i,
  input  logic [MESH_Y_W-1:0] dst_y_i,
  output port_e               out_port_o
);

  logic [MESH_X_W-1:0] my_x;
  logic [MESH_Y_W-1:0] my_y;

  assign my_x = MESH_X_W'(MY_X);
  assign my_y = MESH_Y_W'(MY_Y);

  always_comb begin
    out_port_o = PORT_LOCAL;
    if (dst_x_i != my_x) begin
      // X first, always, and to completion.
      out_port_o = (dst_x_i > my_x) ? PORT_EAST : PORT_WEST;
    end else if (dst_y_i != my_y) begin
      out_port_o = (dst_y_i > my_y) ? PORT_SOUTH : PORT_NORTH;
    end else begin
      out_port_o = PORT_LOCAL;
    end
  end

endmodule : route_compute
