//=============================================================================
// noc_top.sv
//
// 2x2 mesh of routers. Tile n sits at (x,y) = (n[0], n[1]); y increases
// southward, so the tiles are laid out
//
//        x=0    x=1
//  y=0   T0 --- T1
//         |      |
//  y=1   T2 --- T3
//
// Interfaces: clk, rst_n; per tile, an injection port with its credit return
// and an ejection port with its credit return. All are registered inside the
// routers.
//
// The one non-obvious thing: the mesh edges are genuinely unconnected, not
// wrapped. A torus would halve the diameter but would reintroduce cycles in the
// channel dependency graph that XY routing exists to break -- a ring in either
// dimension is a cycle, and escaping it needs either dateline VCs or a
// different routing function. The edge input ports are tied off and an
// assertion checks that nothing is ever routed off-mesh, which is the property
// that makes tying them off safe rather than merely convenient.
//=============================================================================

module noc_top
  import coh_pkg::*;
(
  input  logic                                        clk,
  input  logic                                        rst_n,

  // Injection: tile -> network.
  input  logic  [NUM_TILES-1:0]                       inject_valid_i,
  input  flit_t [NUM_TILES-1:0]                       inject_flit_i,
  output logic  [NUM_TILES-1:0]                       inject_credit_valid_o,
  output logic  [NUM_TILES-1:0][VC_SEL_W-1:0]         inject_credit_vc_o,
  output logic  [NUM_TILES-1:0]                       inject_credit_tail_o,

  // Ejection: network -> tile.
  output logic  [NUM_TILES-1:0]                       eject_valid_o,
  output flit_t [NUM_TILES-1:0]                       eject_flit_o,
  input  logic  [NUM_TILES-1:0]                       eject_credit_valid_i,
  input  logic  [NUM_TILES-1:0][VC_SEL_W-1:0]         eject_credit_vc_i,
  input  logic  [NUM_TILES-1:0]                       eject_credit_tail_i
);

  // Per-router port arrays.
  logic  [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_flit_valid_i;
  flit_t [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_flit_i;
  logic  [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_credit_valid_o;
  logic  [NUM_TILES-1:0][NUM_PORTS-1:0][VC_SEL_W-1:0]   rt_credit_vc_o;
  logic  [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_credit_tail_o;

  logic  [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_flit_valid_o;
  flit_t [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_flit_o;
  logic  [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_credit_valid_i;
  logic  [NUM_TILES-1:0][NUM_PORTS-1:0][VC_SEL_W-1:0]   rt_credit_vc_i;
  logic  [NUM_TILES-1:0][NUM_PORTS-1:0]                 rt_credit_tail_i;

  for (genvar t = 0; t < int'(NUM_TILES); t++) begin : gen_router
    router #(
      .MY_X (t % MESH_X),
      .MY_Y (t / MESH_X)
    ) u_router (
      .clk            (clk),
      .rst_n          (rst_n),
      .flit_valid_i   (rt_flit_valid_i[t]),
      .flit_i         (rt_flit_i[t]),
      .credit_valid_o (rt_credit_valid_o[t]),
      .credit_vc_o    (rt_credit_vc_o[t]),
      .credit_tail_o  (rt_credit_tail_o[t]),
      .flit_valid_o   (rt_flit_valid_o[t]),
      .flit_o         (rt_flit_o[t]),
      .credit_valid_i (rt_credit_valid_i[t]),
      .credit_vc_i    (rt_credit_vc_i[t]),
      .credit_tail_i  (rt_credit_tail_i[t])
    );
  end

  //---------------------------------------------------------------------------
  // Link wiring. For each tile, connect the ports that have a neighbour and tie
  // off the ones that do not. A flit leaving tile A's EAST port arrives at
  // tile B's WEST input; the credit for it travels back the other way.
  //---------------------------------------------------------------------------
  always_comb begin
    // Default everything to idle, then override the connected ports. This is
    // what guarantees an edge port is driven rather than left floating.
    rt_flit_valid_i   = '0;
    rt_flit_i         = '0;
    rt_credit_valid_i = '0;
    rt_credit_vc_i    = '0;
    rt_credit_tail_i  = '0;

    for (int unsigned t = 0; t < NUM_TILES; t++) begin
      automatic int unsigned x = t % MESH_X;
      automatic int unsigned y = t / MESH_X;

      // Local port: the tile itself.
      rt_flit_valid_i[t][PORT_LOCAL]   = inject_valid_i[t];
      rt_flit_i[t][PORT_LOCAL]         = inject_flit_i[t];
      rt_credit_valid_i[t][PORT_LOCAL] = eject_credit_valid_i[t];
      rt_credit_vc_i[t][PORT_LOCAL]    = eject_credit_vc_i[t];
      rt_credit_tail_i[t][PORT_LOCAL]  = eject_credit_tail_i[t];

      // EAST neighbour is t+1 when this tile is not in the rightmost column.
      if (x + 1 < MESH_X) begin
        rt_flit_valid_i[t + 1][PORT_WEST] = rt_flit_valid_o[t][PORT_EAST];
        rt_flit_i[t + 1][PORT_WEST]       = rt_flit_o[t][PORT_EAST];
        rt_credit_valid_i[t][PORT_EAST]   = rt_credit_valid_o[t + 1][PORT_WEST];
        rt_credit_vc_i[t][PORT_EAST]      = rt_credit_vc_o[t + 1][PORT_WEST];
        rt_credit_tail_i[t][PORT_EAST]    = rt_credit_tail_o[t + 1][PORT_WEST];
      end

      // WEST neighbour is t-1 when this tile is not in the leftmost column.
      if (x > 0) begin
        rt_flit_valid_i[t - 1][PORT_EAST] = rt_flit_valid_o[t][PORT_WEST];
        rt_flit_i[t - 1][PORT_EAST]       = rt_flit_o[t][PORT_WEST];
        rt_credit_valid_i[t][PORT_WEST]   = rt_credit_valid_o[t - 1][PORT_EAST];
        rt_credit_vc_i[t][PORT_WEST]      = rt_credit_vc_o[t - 1][PORT_EAST];
        rt_credit_tail_i[t][PORT_WEST]    = rt_credit_tail_o[t - 1][PORT_EAST];
      end

      // SOUTH neighbour is t+MESH_X when this tile is not in the bottom row.
      if (y + 1 < MESH_Y) begin
        rt_flit_valid_i[t + MESH_X][PORT_NORTH] = rt_flit_valid_o[t][PORT_SOUTH];
        rt_flit_i[t + MESH_X][PORT_NORTH]       = rt_flit_o[t][PORT_SOUTH];
        rt_credit_valid_i[t][PORT_SOUTH]        = rt_credit_valid_o[t + MESH_X][PORT_NORTH];
        rt_credit_vc_i[t][PORT_SOUTH]           = rt_credit_vc_o[t + MESH_X][PORT_NORTH];
        rt_credit_tail_i[t][PORT_SOUTH]         = rt_credit_tail_o[t + MESH_X][PORT_NORTH];
      end

      // NORTH neighbour is t-MESH_X when this tile is not in the top row.
      if (y > 0) begin
        rt_flit_valid_i[t - MESH_X][PORT_SOUTH] = rt_flit_valid_o[t][PORT_NORTH];
        rt_flit_i[t - MESH_X][PORT_SOUTH]       = rt_flit_o[t][PORT_NORTH];
        rt_credit_valid_i[t][PORT_NORTH]        = rt_credit_valid_o[t - MESH_X][PORT_SOUTH];
        rt_credit_vc_i[t][PORT_NORTH]           = rt_credit_vc_o[t - MESH_X][PORT_SOUTH];
        rt_credit_tail_i[t][PORT_NORTH]         = rt_credit_tail_o[t - MESH_X][PORT_SOUTH];
      end
    end
  end

  //---------------------------------------------------------------------------
  // Ejection
  //---------------------------------------------------------------------------
  always_comb begin
    for (int unsigned t = 0; t < NUM_TILES; t++) begin
      eject_valid_o[t]         = rt_flit_valid_o[t][PORT_LOCAL];
      eject_flit_o[t]          = rt_flit_o[t][PORT_LOCAL];
      inject_credit_valid_o[t] = rt_credit_valid_o[t][PORT_LOCAL];
      inject_credit_vc_o[t]    = rt_credit_vc_o[t][PORT_LOCAL];
      inject_credit_tail_o[t]  = rt_credit_tail_o[t][PORT_LOCAL];
    end
  end

`ifndef SYNTHESIS
  // Nothing may ever be routed off the edge of the mesh. If one of these fires,
  // route_compute produced a direction that does not exist at this tile, which
  // in a 2x2 can only mean a corrupted destination coordinate.
  for (genvar t = 0; t < int'(NUM_TILES); t++) begin : gen_edge_asserts
    if ((t % MESH_X) + 1 >= int'(MESH_X)) begin : gen_no_east
      a_no_east_escape : assert property (@(posedge clk) disable iff (!rst_n)
        !rt_flit_valid_o[t][PORT_EAST])
        else $error("noc_top: tile %0d routed a flit off the east edge", t);
    end
    if ((t % MESH_X) == 0) begin : gen_no_west
      a_no_west_escape : assert property (@(posedge clk) disable iff (!rst_n)
        !rt_flit_valid_o[t][PORT_WEST])
        else $error("noc_top: tile %0d routed a flit off the west edge", t);
    end
    if ((t / MESH_X) + 1 >= int'(MESH_Y)) begin : gen_no_south
      a_no_south_escape : assert property (@(posedge clk) disable iff (!rst_n)
        !rt_flit_valid_o[t][PORT_SOUTH])
        else $error("noc_top: tile %0d routed a flit off the south edge", t);
    end
    if ((t / MESH_X) == 0) begin : gen_no_north
      a_no_north_escape : assert property (@(posedge clk) disable iff (!rst_n)
        !rt_flit_valid_o[t][PORT_NORTH])
        else $error("noc_top: tile %0d routed a flit off the north edge", t);
    end

    // A flit ejected at a tile must be addressed to that tile.
    a_eject_is_mine : assert property (@(posedge clk) disable iff (!rst_n)
      eject_valid_o[t] |-> ((eject_flit_o[t].dst_x == MESH_X_W'(t % MESH_X)) &&
                            (eject_flit_o[t].dst_y == MESH_Y_W'(t / MESH_X))))
      else $error("noc_top: tile %0d ejected a flit addressed to (%0d,%0d)", t, eject_flit_o[t].dst_x, eject_flit_o[t].dst_y);
  end
`endif

endmodule : noc_top
