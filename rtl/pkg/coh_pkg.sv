//=============================================================================
// coh_pkg.sv
//
// Single source of truth for the directory-MESI + NoC subsystem: every sizing
// parameter, the address decode, the L1 and directory state enums, the message
// and virtual-network enums, and the flit / head-payload structs.
//
// Interfaces: none (package). Imported by every module under rtl/.
//
// The one non-obvious thing: the L1 tag is NON-CONTIGUOUS. The home-bank bits
// addr[6:5] sit *below* the L1 index field, so they are neither covered by the
// index nor implied by the cache instance. If they are not carried in the tag,
// two distinct lines whose addresses differ only in home bank land in the same
// L1 set with the same tag and alias onto each other. See docs/decisions.md,
// "L1 tag must include the home-bank bits".
//=============================================================================

package coh_pkg;

  //---------------------------------------------------------------------------
  // System geometry
  //---------------------------------------------------------------------------
  localparam int unsigned NUM_TILES   = 4;
  localparam int unsigned MESH_X      = 2;
  localparam int unsigned MESH_Y      = 2;
  localparam int unsigned TILE_ID_W   = $clog2(NUM_TILES);   // 2
  localparam int unsigned MESH_X_W    = $clog2(MESH_X);      // 1
  localparam int unsigned MESH_Y_W    = $clog2(MESH_Y);      // 1

  // Tile that hosts the main-memory controller on its local port.
  localparam int unsigned MEM_TILE_ID = 3;

  //---------------------------------------------------------------------------
  // Address geometry
  //---------------------------------------------------------------------------
  localparam int unsigned ADDR_W      = 32;
  localparam int unsigned LINE_BYTES  = 32;
  localparam int unsigned LINE_W      = LINE_BYTES * 8;              // 256
  localparam int unsigned WORD_W      = 32;
  localparam int unsigned BE_W        = WORD_W / 8;                  // 4
  localparam int unsigned WORDS_PER_LINE = LINE_W / WORD_W;          // 8

  localparam int unsigned OFFSET_W    = $clog2(LINE_BYTES);          // 5
  localparam int unsigned BANK_W      = $clog2(NUM_TILES);           // 2
  localparam int unsigned LINE_ADDR_W = ADDR_W - OFFSET_W;           // 27
  localparam int unsigned WORD_SEL_W  = $clog2(WORDS_PER_LINE);      // 3

  // Address field positions, from LSB:
  //   [4:0]   block offset
  //   [6:5]   home bank id
  //   [12:7]  L1 / L2 set index
  //   [31:13] upper tag
  localparam int unsigned BANK_LSB    = OFFSET_W;                    // 5
  localparam int unsigned IDX_LSB     = OFFSET_W + BANK_W;           // 7

  //---------------------------------------------------------------------------
  // L1 data cache: 64 sets x 2 ways x 32 B = 4 KB, PIPT, write-back/write-alloc
  //---------------------------------------------------------------------------
  localparam int unsigned L1_SETS     = 64;
  localparam int unsigned L1_WAYS     = 2;
  localparam int unsigned L1_IDX_W    = $clog2(L1_SETS);             // 6
  localparam int unsigned L1_WAY_W    = $clog2(L1_WAYS);             // 1

  // Upper tag bits are everything above the index field.
  localparam int unsigned L1_UTAG_W   = ADDR_W - (IDX_LSB + L1_IDX_W); // 19
  // Full L1 tag = upper tag ++ home-bank bits. See the header note.
  localparam int unsigned L1_TAG_W    = L1_UTAG_W + BANK_W;            // 21

  //---------------------------------------------------------------------------
  // L2 / directory bank: 64 sets x 8 ways x 32 B = 16 KB per bank, inclusive.
  // A bank only ever sees addresses whose home-bank field selects it, so the
  // bank bits are implied by the instance and are NOT stored in the L2 tag.
  //---------------------------------------------------------------------------
  localparam int unsigned L2_SETS     = 64;
  localparam int unsigned L2_WAYS     = 8;
  localparam int unsigned L2_IDX_W    = $clog2(L2_SETS);             // 6
  localparam int unsigned L2_WAY_W    = $clog2(L2_WAYS);             // 3
  localparam int unsigned L2_TAG_W    = ADDR_W - (IDX_LSB + L2_IDX_W); // 19

  //---------------------------------------------------------------------------
  // MSHR / TBE
  //---------------------------------------------------------------------------
  localparam int unsigned MSHR_ENTRIES = 4;
  localparam int unsigned MSHR_IDX_W   = $clog2(MSHR_ENTRIES);       // 2
  localparam int unsigned TBE_ENTRIES  = 4;
  localparam int unsigned TBE_IDX_W    = $clog2(TBE_ENTRIES);        // 2
  localparam int unsigned CORE_TAG_W   = 4;

  // ack_cnt is SIGNED: Inv-Acks may arrive before the Data that carries the
  // AckCount, driving the count negative before it is credited back up.
  // Range must hold -(NUM_TILES-1) .. +(NUM_TILES-1) with sign bit.
  localparam int unsigned ACK_CNT_W    = 4;
  // AckCount as carried in a head flit is unsigned, 0 .. NUM_TILES-1.
  localparam int unsigned ACK_FIELD_W  = 3;

  //---------------------------------------------------------------------------
  // Network
  //---------------------------------------------------------------------------
  localparam int unsigned NUM_VNETS       = 3;
  localparam int unsigned VNET_W          = $clog2(NUM_VNETS + 1);   // 2
  localparam int unsigned VCS_PER_VNET    = 2;
  localparam int unsigned VC_ID_W         = $clog2(VCS_PER_VNET);    // 1
  localparam int unsigned VCS_PER_PORT    = NUM_VNETS * VCS_PER_VNET;// 6
  localparam int unsigned VC_SEL_W        = $clog2(VCS_PER_PORT);    // 3
  localparam int unsigned VC_DEPTH        = 4;
  localparam int unsigned CREDIT_W        = $clog2(VC_DEPTH + 1);    // 3
  localparam int unsigned VC_PTR_W        = $clog2(VC_DEPTH);        // 2

  localparam int unsigned NUM_PORTS       = 5;                       // N E S W L
  localparam int unsigned PORT_W          = $clog2(NUM_PORTS);       // 3

  localparam int unsigned FLIT_PAYLOAD_W  = 128;
  localparam int unsigned FLITS_PER_LINE  = LINE_W / FLIT_PAYLOAD_W; // 2

  //---------------------------------------------------------------------------
  // Memory model
  //---------------------------------------------------------------------------
  localparam int unsigned MEM_LATENCY = 20;

  //---------------------------------------------------------------------------
  // Liveness bounds. These are assertion limits, not testbench watchdogs.
  //---------------------------------------------------------------------------
  localparam int unsigned MSHR_TIMEOUT = 1000;
  localparam int unsigned TBE_TIMEOUT  = 500;
  localparam int unsigned SINK_BOUND   = 8;

  //---------------------------------------------------------------------------
  // Router port encoding
  //---------------------------------------------------------------------------
  typedef enum logic [PORT_W-1:0] {
    PORT_NORTH = 3'd0,
    PORT_EAST  = 3'd1,
    PORT_SOUTH = 3'd2,
    PORT_WEST  = 3'd3,
    PORT_LOCAL = 3'd4
  } port_e;

  //---------------------------------------------------------------------------
  // Virtual networks. Assigned by dependency depth: a request may cause a
  // forward, a forward may cause a response, a response causes nothing.
  //---------------------------------------------------------------------------
  typedef enum logic [VNET_W-1:0] {
    VN0_REQ  = 2'd0,   // may stall arbitrarily
    VN1_FWD  = 2'd1,   // may stall on a transient L1 state
    VN2_RSP  = 2'd2    // must never stall: this is the sink
  } vnet_e;

  //---------------------------------------------------------------------------
  // Per-input-VC state in a router input unit.
  //   I = empty
  //   R = head buffered and routed, awaiting an output VC
  //   V = output VC granted, head not yet sent
  //   A = head sent, body/tail flowing
  //---------------------------------------------------------------------------
  typedef enum logic [1:0] {
    VC_IDLE    = 2'd0,
    VC_ROUTED  = 2'd1,
    VC_ALLOC   = 2'd2,
    VC_ACTIVE  = 2'd3
  } vc_state_e;

  //---------------------------------------------------------------------------
  // L1 coherence states: 4 stable + 9 transient.
  // Superscript A = awaiting acks, D = awaiting data (Sorin/Hill/Wood primer).
  //---------------------------------------------------------------------------
  typedef enum logic [3:0] {
    L1_I      = 4'd0,
    L1_S      = 4'd1,
    L1_E      = 4'd2,
    L1_M      = 4'd3,
    L1_IS_D   = 4'd4,   // issued GetS, awaiting data
    L1_IM_AD  = 4'd5,   // issued GetM, awaiting data and acks
    L1_IM_A   = 4'd6,   // data in, acks outstanding
    L1_SM_AD  = 4'd7,   // upgrade issued, awaiting data/AckCount and acks
    L1_SM_A   = 4'd8,   // AckCount known, acks outstanding
    L1_MI_A   = 4'd9,   // PutM issued, awaiting Put-Ack
    L1_EI_A   = 4'd10,  // PutE issued, awaiting Put-Ack
    L1_SI_A   = 4'd11,  // PutS issued, awaiting Put-Ack
    L1_II_A   = 4'd12   // evicting, invalidated mid-flight, awaiting Put-Ack
  } l1_state_e;

  //---------------------------------------------------------------------------
  // Directory states. S_D = had M or E, forwarded a GetS, waiting for the
  // owner's data before it can serve anyone else.
  //---------------------------------------------------------------------------
  typedef enum logic [2:0] {
    DIR_I   = 3'd0,
    DIR_S   = 3'd1,
    DIR_E   = 3'd2,
    DIR_M   = 3'd3,
    DIR_S_D = 3'd4
  } dir_state_e;

  //---------------------------------------------------------------------------
  // Transaction-buffer-entry states at the directory.
  //---------------------------------------------------------------------------
  typedef enum logic [1:0] {
    TBE_INVALID   = 2'd0,
    TBE_MEM_PEND  = 2'd1,   // L2 miss, awaiting memory data
    TBE_INV_PEND  = 2'd2,   // back-invalidation in flight
    TBE_WB_PEND   = 2'd3    // dirty victim being written to memory
  } tbe_state_e;

  //---------------------------------------------------------------------------
  // Message types. 5 bits, not 4: the full set is exactly 18 and a 4-bit field
  // leaves zero headroom for the Recall added in the inclusion phase. The head
  // flit uses 37 of 128 payload bits, so the extra bit is free.
  //---------------------------------------------------------------------------
  typedef enum logic [4:0] {
    // ---- VN0: requests (L1 -> directory, directory -> memory) ----
    MSG_GETS       = 5'd0,
    MSG_GETM       = 5'd1,
    MSG_PUTS       = 5'd2,
    MSG_PUTM       = 5'd3,   // carries data
    MSG_PUTE       = 5'd4,
    MSG_MEM_READ   = 5'd5,
    MSG_MEM_WRITE  = 5'd6,   // carries data

    // ---- VN1: forwards / interventions (directory -> L1) ----
    MSG_FWD_GETS   = 5'd8,
    MSG_FWD_GETM   = 5'd9,
    MSG_INV        = 5'd10,
    MSG_PUT_ACK    = 5'd11,
    MSG_RECALL     = 5'd12,  // back-invalidation; reuses the Fwd-GetM arc at L1

    // ---- VN2: responses (never stalls) ----
    MSG_DATA_DIR   = 5'd16,  // data from directory, carries AckCount
    MSG_DATA_E     = 5'd17,  // exclusive data from directory
    MSG_DATA_OWNER = 5'd18,  // data forwarded owner -> requester
    MSG_INV_ACK    = 5'd19,
    MSG_WB_DATA    = 5'd20,  // owner/L1 data back to the directory
    MSG_MEM_DATA   = 5'd21   // memory -> directory fill
  } msg_type_e;

  //---------------------------------------------------------------------------
  // Core request opcodes driven by req_gen.
  //---------------------------------------------------------------------------
  typedef enum logic [1:0] {
    OP_LD         = 2'd0,
    OP_ST         = 2'd1,
    OP_EVICT_HINT = 2'd2
  } core_op_e;

  //---------------------------------------------------------------------------
  // Flit. Wormhole with VCs: a control packet is one head+tail flit, a data
  // packet is head + FLITS_PER_LINE body/tail flits.
  //---------------------------------------------------------------------------
  typedef struct packed {
    logic                       head;
    logic                       tail;
    vnet_e                      vnet;
    logic [VC_ID_W-1:0]         vc_id;
    logic [MESH_X_W-1:0]        dst_x;
    logic [MESH_Y_W-1:0]        dst_y;
    logic [TILE_ID_W-1:0]       src_id;
    logic [FLIT_PAYLOAD_W-1:0]  payload;
  } flit_t;

  localparam int unsigned FLIT_W = $bits(flit_t);

  //---------------------------------------------------------------------------
  // Head-flit control payload. Overlaid on flit_t.payload for head flits.
  //---------------------------------------------------------------------------
  typedef struct packed {
    msg_type_e                  msg_type;   // 5
    logic [LINE_ADDR_W-1:0]     addr;       // 27
    logic [TILE_ID_W-1:0]       requester;  // 2  (for forwards: who to answer)
    logic [ACK_FIELD_W-1:0]     ack_count;  // 3  (for Data from directory)
  } head_payload_t;

  localparam int unsigned HEAD_PAYLOAD_W = $bits(head_payload_t);
  localparam int unsigned HEAD_PAD_W     = FLIT_PAYLOAD_W - HEAD_PAYLOAD_W;

  //---------------------------------------------------------------------------
  // Address decode helpers. Every consumer uses these; no module slices a raw
  // address itself.
  //---------------------------------------------------------------------------
  function automatic logic [BANK_W-1:0] addr_home_bank(input logic [ADDR_W-1:0] a);
    return a[BANK_LSB +: BANK_W];
  endfunction

  function automatic logic [L1_IDX_W-1:0] addr_l1_index(input logic [ADDR_W-1:0] a);
    return a[IDX_LSB +: L1_IDX_W];
  endfunction

  // Non-contiguous on purpose: upper tag ++ home-bank bits.
  function automatic logic [L1_TAG_W-1:0] addr_l1_tag(input logic [ADDR_W-1:0] a);
    return {a[ADDR_W-1 -: L1_UTAG_W], a[BANK_LSB +: BANK_W]};
  endfunction

  function automatic logic [L2_IDX_W-1:0] addr_l2_index(input logic [ADDR_W-1:0] a);
    return a[IDX_LSB +: L2_IDX_W];
  endfunction

  function automatic logic [L2_TAG_W-1:0] addr_l2_tag(input logic [ADDR_W-1:0] a);
    return a[ADDR_W-1 -: L2_TAG_W];
  endfunction

  function automatic logic [LINE_ADDR_W-1:0] addr_line(input logic [ADDR_W-1:0] a);
    return a[ADDR_W-1 -: LINE_ADDR_W];
  endfunction

  // Line address -> the same fields, for message handling where the offset is
  // already stripped.
  function automatic logic [BANK_W-1:0] line_home_bank(input logic [LINE_ADDR_W-1:0] l);
    return l[0 +: BANK_W];
  endfunction

  function automatic logic [L1_IDX_W-1:0] line_l1_index(input logic [LINE_ADDR_W-1:0] l);
    return l[BANK_W +: L1_IDX_W];
  endfunction

  function automatic logic [L1_TAG_W-1:0] line_l1_tag(input logic [LINE_ADDR_W-1:0] l);
    return {l[LINE_ADDR_W-1 -: L1_UTAG_W], l[0 +: BANK_W]};
  endfunction

  function automatic logic [L2_IDX_W-1:0] line_l2_index(input logic [LINE_ADDR_W-1:0] l);
    return l[BANK_W +: L2_IDX_W];
  endfunction

  function automatic logic [L2_TAG_W-1:0] line_l2_tag(input logic [LINE_ADDR_W-1:0] l);
    return l[LINE_ADDR_W-1 -: L2_TAG_W];
  endfunction

  // Mesh coordinates of a tile id: (x,y) = (id[0], id[1]).
  function automatic logic [MESH_X_W-1:0] tile_x(input logic [TILE_ID_W-1:0] id);
    return id[0 +: MESH_X_W];
  endfunction

  function automatic logic [MESH_Y_W-1:0] tile_y(input logic [TILE_ID_W-1:0] id);
    return id[MESH_X_W +: MESH_Y_W];
  endfunction

  // A VC's index within a port is {vnet, vc_id}. VCS_PER_VNET is a power of two
  // so this is concatenation, not arithmetic.
  function automatic logic [VC_SEL_W-1:0] vc_index(
      input vnet_e vn, input logic [VC_ID_W-1:0] vc);
    return {VC_SEL_W'(vn), vc}[VC_SEL_W-1:0];
  endfunction

  function automatic vnet_e vc_to_vnet(input logic [VC_SEL_W-1:0] idx);
    return vnet_e'(idx[VC_SEL_W-1 : VC_ID_W]);
  endfunction

  function automatic logic [VC_ID_W-1:0] vc_to_id(input logic [VC_SEL_W-1:0] idx);
    return idx[VC_ID_W-1:0];
  endfunction

  function automatic logic is_stable_l1(input l1_state_e s);
    return (s == L1_I) || (s == L1_S) || (s == L1_E) || (s == L1_M);
  endfunction

endpackage : coh_pkg
