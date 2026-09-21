"""Microarchitecture diagrams: the tile, the L1, the directory, the router."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from svg import Svg, VNET, INK, MUTED, RULE, ROLE

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "img")

V0, V1, V2 = VNET[0][0], VNET[1][0], VNET[2][0]


def vnet_legend(s, x, y):
    s.legend(x, y, [
        (V0, "VN0  requests   GetS, GetM, PutS, PutM, PutE"),
        (V1, "VN1  forwards   Fwd-GetS, Fwd-GetM, Inv, Recall, Put-Ack"),
        (V2, "VN2  responses  Data, Inv-Ack, WB-Data, Recall-Ack"),
    ], title="Virtual networks, numbered by dependency depth", mono=True)


# --------------------------------------------------------------- tile uarch --
def tile(path):
    s = Svg(1240, 1010,
            "Tile microarchitecture — who talks on which virtual network",
            "Two independent protocol agents share one router port. The "
            "deadlock argument is visible here: three structurally separate "
            "networks, and nothing on VN2 ever waits.")

    s.block(125, 92, 210, 44, "core port", role="external", size=12,
            sub=("one request in flight",))
    s.block(990, 92, 210, 44, "mem_model", role="external", size=12,
            sub=("8-cycle line read / write",))

    # ---- L1 agent -----------------------------------------------------------
    s.group_box(40, 158, 520, 296, "L1 agent  —  requester", role="control")
    s.block(64, 190, 220, 60, "l1_cache", role="control", size=13,
            sub=("3-stage pipeline, PIPT", "4 KB, 64 sets x 2 ways"))
    s.block(304, 190, 232, 60, "mshr_file", role="storage", size=13,
            sub=("4 entries, CAM on line addr", "signed ack_cnt"))
    s.block(64, 266, 220, 46, "tag + state array", role="storage", size=12,
            sub=("the state, transient included",))
    s.block(304, 266, 232, 46, "data array", role="storage", size=12,
            sub=("2 x 32 B per set",))
    s.block(64, 328, 472, 44, "l1_coh_fsm", role="datapath", size=12,
            sub=("pure function (state, event) -> next state + actions, 13 states",))
    s.text(300, 394, "issues on VN0 · accepts VN1 and VN2 · answers on VN2",
           size=11, fill=MUTED, mono=False)
    s.text(300, 412, "coherence traffic outranks the core request, always",
           size=11, fill=MUTED, mono=False)
    s.text(300, 434, "three writers of a line's state: S1, the VN1 handler, "
           "the VN2 handler", size=10.5, fill="#8d3b40", mono=False)

    # ---- Directory agent ----------------------------------------------------
    s.group_box(680, 158, 520, 296,
                "Directory agent  —  home bank for addr[6:5]", role="datapath")
    s.block(704, 190, 220, 60, "dir_ctrl", role="control", size=13,
            sub=("one request at a time,", "VN0 head held until commit"))
    s.block(944, 190, 232, 60, "tbe_file", role="storage", size=13,
            sub=("4 entries, CAM on line addr", "outstanding ack count"))
    s.block(704, 266, 220, 46, "l2_bank", role="storage", size=12,
            sub=("16 KB, 64 sets x 8 ways",))
    s.block(944, 266, 232, 46, "directory metadata", role="storage", size=12,
            sub=("owner, sharer vector, 5 states",))
    s.block(704, 328, 472, 44, "dir_coh_fsm", role="datapath", size=12,
            sub=("pure function (dir state, event) -> next state + actions, 5 states",))
    s.text(940, 394, "accepts VN0 and VN2 · forwards on VN1 · answers on VN2",
           size=11, fill=MUTED, mono=False)
    s.text(940, 412, "strictly inclusive: an L2 eviction recalls the L1 copies",
           size=11, fill=MUTED, mono=False)
    s.text(940, 434, "VN2 in is a true sink — buffered, never back-pressured",
           size=10.5, fill="#1f5a86", mono=False)

    # ---- message holds, on the outbound paths only --------------------------
    for x, lab, col in ((70, "hold_vn0", V0), (250, "hold_vn2", V2),
                        (680, "hold_vn1", V1), (860, "hold_vn2_dir", V2)):
        s.rect(x, 512, 150, 40, fill="#ffffff", stroke=col, rx=6, sw=1.6,
               dash="4 3")
        s.text(x + 75, 537, lab, size=11.5, fill=col, weight="600")
    s.text(620, 532, "msg_hold", size=11.5, weight="700", fill=MUTED)
    s.text(620, 548, "registered", size=9.5, fill=MUTED)

    # ---- NIC and router -----------------------------------------------------
    s.block(40, 600, 1160, 96, "tile_nic", role="iface", size=14, label_dy=30)
    s.lines(620, 648, [
        "three separate packetisers, three separate reassembly paths, three separate credit pools",
        "only the physical flit port is shared, and it is arbitrated per FLIT, not per packet",
        "a blocked VN0 can never hold the injection path a VN2 response needs",
    ], size=10.5, fill=MUTED, mono=False, gap=15)

    s.block(40, 730, 1160, 62, "router  —  local port", role="control", size=13,
            label_dy=26)
    s.text(620, 774, "5 ports x 6 VCs (2 per vnet), XY routing, credit flow control",
           size=10.5, fill=MUTED, mono=False)

    # ---- the eight message ports --------------------------------------------
    out_ports = ((145, V0, "VN0 out", "requests"),
                 (325, V2, "VN2 out", "data, Inv-Ack"),
                 (755, V1, "VN1 out", "forwards, Put-Ack"),
                 (935, V2, "VN2 out", "data, Recall-Ack"))
    in_ports = ((440, V1, "VN1 in", "forwards"),
                (520, V2, "VN2 in", "responses"),
                (1060, V0, "VN0 in", "requests"),
                (1150, V2, "VN2 in", "responses"))
    for x, col, lab, sub in out_ports:
        s.arrow([(x, 454), (x, 512)], color=col, sw=2.0)
        s.arrow([(x, 552), (x, 600)], color=col, sw=2.0)
        s.text(x + 8, 476, lab, size=10, anchor="start", fill=col, weight="600")
        s.text(x + 8, 489, sub, size=9, anchor="start", fill=MUTED)
    for x, col, lab, sub in in_ports:
        s.arrow([(x, 600), (x, 454)], color=col, sw=2.0, dash="6 3")
        s.text(x + 8, 500, lab, size=10, anchor="start", fill=col, weight="600")
        s.text(x + 8, 513, sub, size=9, anchor="start", fill=MUTED)

    s.arrow([(230, 136), (230, 190)], color=INK, sw=1.6, both=True)
    s.arrow([(1095, 136), (1095, 190)], color=INK, sw=1.6, both=True)

    # ---- off-tile -----------------------------------------------------------
    s.arrow([(620, 792), (620, 828)], color=INK, sw=2.2, both=True)
    s.text(620, 846, "N / S / E / W links  —  flit out, credit back",
           size=11, fill=MUTED)

    vnet_legend(s, 40, 890)
    s.legend(640, 890, [
        (ROLE["storage"][1], "storage: arrays, MSHR/TBE files"),
        (ROLE["control"][1], "control: pipelines, FSM wrappers"),
        (ROLE["datapath"][1], "datapath: the pure protocol tables"),
        (ROLE["iface"][1], "interface: packetisation boundary"),
    ], title="Blocks, by role", cols=1, mono=False)
    s.note(900, 900, [
        "Solid = sent by this tile.  Dashed = received.",
        "hold_vn* is an ordinary pipeline register at hold=0;",
        "above 0 a directed test forces an exact interleaving",
        "without changing the hardware under test (D16).",
        "The directory's responses hold separately from the L1's,",
        "because race R1 needs Data to land after the Inv-Acks.",
    ])
    return s.write(path)


_MAIN = True


# ------------------------------------------------------------- L1 pipeline --
def l1_pipeline(path):
    s = Svg(1240, 960, "L1 cache — pipeline, and the order work is let in",
            "Three stages for the core's request, and three other classes of "
            "work that outrank it. The priority order is the deadlock "
            "argument, not a performance tuning knob.")

    # ---- the three stages ---------------------------------------------------
    s.block(60, 132, 250, 92, "S0   issue", role="control", size=14,
            label_dy=34,
            sub=("pick the replay or a new request,",
                 "launch the tag and data read"))
    s.block(360, 132, 320, 92, "S1   compare and decide", role="control",
            size=14, label_dy=34,
            sub=("tag compare, victim select,",
                 "l1_coh_fsm lookup, MSHR allocate"))
    s.block(730, 132, 250, 92, "S2   store commit", role="control", size=14,
            label_dy=34,
            sub=("merge the word into the line,", "write the data array"))
    s.arrow([(310, 178), (360, 178)], color=INK, sw=2.0)
    s.arrow([(680, 178), (730, 178)], color=INK, sw=2.0)
    s.text(1105, 150, "one request in the pipe,", size=11, fill=MUTED,
           mono=False)
    s.text(1105, 166, "but up to four transactions", size=11, fill=MUTED,
           mono=False)
    s.text(1105, 182, "outstanding in the MSHR file", size=11, fill=MUTED,
           mono=False)

    # ---- arrays -------------------------------------------------------------
    s.block(360, 268, 150, 56, "tag + state", role="storage", size=12,
            sub=("64 x 2",))
    s.block(530, 268, 150, 56, "data array", role="storage", size=12,
            sub=("64 x 2 x 32 B",))
    s.block(730, 268, 250, 56, "mshr_file", role="storage", size=12,
            sub=("4 entries · CAM on line address",))
    s.arrow([(420, 268), (420, 224)], color=MUTED, sw=1.6, label="read",
            label_side=-1)
    s.arrow([(590, 268), (590, 224)], color=MUTED, sw=1.6)
    s.arrow([(660, 224), (660, 238), (855, 238), (855, 268)], color=MUTED,
            sw=1.6)
    s.text(770, 232, "allocate", size=10, fill=MUTED)
    s.arrow([(820, 224), (820, 256), (650, 256), (650, 268)], color=MUTED,
            sw=1.6)
    s.text(644, 250, "write", size=10, anchor="end", fill=MUTED)

    # ---- what S1 decides ----------------------------------------------------
    s.text(1180, 340, "S1 has exactly four outcomes", size=12.5, anchor="end",
           weight="650")
    outcomes = [
        (60, "hit", ("the table says done —", "answer the core now"), "#5a9e68"),
        (340, "allocate", ("miss or upgrade — take an MSHR,",
                           "send GetS / GetM on VN0"), V0),
        (620, "evict", ("victim is not I — its OWN MSHR,",
                        "send PutS / PutE / PutM"), V0),
        (900, "replay", ("anything else — S0 re-issues it,",
                         "no state was changed"), "#c2565c"),
    ]
    s.arrow([(520, 224), (520, 356), (185, 356), (185, 388)], color=INK,
            sw=1.4)
    s.raw(f'<path d="M 185,356 L 1025,356" fill="none" stroke="{INK}" '
          f'stroke-width="1.4"/>')
    for x, lab, why, col in outcomes:
        if x != 60:
            s.arrow([(x + 125, 356), (x + 125, 388)], color=INK, sw=1.4)
        s.rect(x, 388, 250, 62, fill="#ffffff", stroke=col, rx=7, sw=1.7)
        s.text(x + 125, 412, lab, size=13, weight="700", fill=col)
        s.lines(x + 125, 428, why, size=10, fill=MUTED, mono=False, gap=13)

    # ---- the replay loop ----------------------------------------------------
    s.arrow([(1025, 450), (1025, 482), (140, 482), (140, 224)],
            color="#c2565c", sw=1.8)
    s.text(600, 502, "an eviction REPLAYS too: issuing the Put only frees the "
           "way, it does not serve the request  (bug B9)",
           size=10.5, fill="#c2565c", mono=False)

    # ---- priority ladder ----------------------------------------------------
    s.text(60, 542, "Who gets the array port, the MSHR update port and the "
           "VN0 queue — highest first", size=13, anchor="start", weight="650")
    rows = [
        ("1", "VN2 response", V2, "may never be refused",
         "it is the sink the whole dependency argument rests on, so it takes "
         "the MSHR update port outright"),
        ("2", "retirement", "#5a9e68", "cannot be refused either",
         "the transaction is already complete; holding it would hold an MSHR "
         "another core is waiting on"),
        ("3", "VN1 forward", V1, "may stall, on a transient state only",
         "stalling a forward only delays this tile; the directory that sent "
         "it is not blocked behind it"),
        ("4", "core request", MUTED, "lowest, and may stall for any reason",
         "a core that keeps issuing can never starve the responses that let "
         "the other three cores finish"),
    ]
    y = 564
    for n, name, col, rule, why in rows:
        s.rect(60, y, 1120, 56, fill="#ffffff", stroke=col, rx=7, sw=1.6)
        s.rect(60, y, 8, 56, fill=col, stroke=col, rx=3)
        s.text(92, y + 34, n, size=17, weight="700", fill=col)
        s.text(120, y + 25, name, size=13, anchor="start", weight="650",
               fill=col)
        s.text(120, y + 42, rule, size=10, anchor="start", fill=MUTED,
               mono=False)
        s.text(1160, y + 34, why, size=10.5, anchor="end", fill=INK,
               mono=False)
        y += 64

    s.rect(60, 836, 1120, 92, fill="#fbe6e6", stroke="#c2565c", rx=8)
    s.text(84, 862, "The rule that is easy to get wrong", size=12.5,
           anchor="start", weight="700", fill="#8d3b40")
    s.note(84, 882, [
        "Priority is not enough. S1 must also YIELD to the forward and response handlers when they are "
        "working on the same line.",
        "All three write a line's state at the same edge and S1's write lands last, so without that rule S1 "
        "silently undoes a transition",
        "that has already been announced to the rest of the machine. See bug B20, decision D23, and the "
        "next diagram.",
    ])
    return s.write(path)


# --------------------------------------------------- three writers of state --
def l1_state_writers(path):
    s = Svg(1240, 880,
            "Three writers of one line's coherence state — and the rule that "
            "orders them",
            "The tag array is the single source of truth for a line's state, "
            "transient states included. Three units write it, all on the same "
            "clock edge.")

    writers = [
        (60, "S1  —  core pipeline", MUTED,
         ("installs a transient state on a miss,", "an upgrade, or an eviction"),
         "writes LAST in the always_ff, so it wins by default"),
        (460, "VN1 forward handler", V1,
         ("M -> S on a Fwd-GetS, anything -> I", "on an Inv or a Recall"),
         "has already sent the data or the ack away"),
        (860, "VN2 response handler", V2,
         ("IM_AD -> IM_A, SM_AD -> SM_A,", "transient -> stable at retirement"),
         "is a sink: it cannot be refused or deferred"),
    ]
    # Each writer lands on its own point of the array, on its own horizontal
    # leg, so three colours stay three colours instead of merging into a bus.
    legs = {60: (224, 500), 460: (None, 620), 860: (238, 740)}
    for x, name, col, sub, why in writers:
        s.rect(x, 108, 320, 84, fill="#ffffff", stroke=col, rx=8, sw=1.8)
        s.text(x + 160, 134, name, size=13, weight="700", fill=col)
        s.lines(x + 160, 152, sub, size=10.5, fill=MUTED, mono=False, gap=14)
        s.text(x + 160, 186, why, size=9.5, fill=col, mono=False)
        leg_y, tgt = legs[x]
        pts = ([(x + 160, 192), (tgt, 268)] if leg_y is None else
               [(x + 160, 192), (x + 160, leg_y), (tgt, leg_y), (tgt, 268)])
        s.arrow(pts, color=col, sw=2.0)

    s.block(400, 268, 440, 62, "tag + state array", role="storage", size=14,
            sub=("one state field per way — 13 values, transient included",))

    s.rect(60, 366, 1120, 78, fill="#fbe6e6", stroke="#c2565c", rx=8)
    s.text(84, 392, "Winning by default is the bug", size=12.5,
           anchor="start", weight="700", fill="#8d3b40")
    s.note(84, 412, [
        "All three read the state combinationally and write it at the same edge. S1's assignment is later in "
        "the always_ff, so S1 overwrites",
        "whatever the other two decided — and they have already put a message on the wire that says otherwise.",
    ])

    s.rect(60, 470, 1120, 92, fill="#eef4fa", stroke=V2, rx=8)
    s.text(84, 496, "The rule  (decision D23)", size=12.5, anchor="start",
           weight="700", fill="#1f5a86")
    s.text(84, 522, "s1_coh_conflict = (VN1 presented for this set and way)  "
           "||  (VN2 taken for this set and way)", size=11.5, anchor="start",
           fill=INK)
    s.text(84, 546, "and s1_coh_conflict suppresses s1_resp, s1_alloc and "
           "s1_evict — so the request REPLAYS and the coherence write stands.",
           size=10.5, anchor="start", fill=MUTED, mono=False)

    panels = [
        (60, "What S1 clobbered on VN1", V1,
         ["A Fwd-GetS is accepted for a line in M in the same",
          "cycle as a store HIT on that line.",
          "",
          "The handler sends the data to the requester and moves",
          "the line to S. S1 writes M back. The cache then evicts",
          "with a PutM a line the directory has just told everyone",
          "is shared, and the Inv that follows lands in MI_A,",
          "which has no arc for it.",
          "",
          "Caught by: test_stress racing configuration -> B20"]),
        (640, "What S1 clobbered on VN2", V2,
         ["A load HITS a line in SM_AD — legal, a shared copy is",
          "still readable while the upgrade is outstanding.",
          "",
          "In the same cycle Data+AckCount arrives and writes",
          "SM_A. S1 writes SM_AD back. The ack count then reaches",
          "zero in a state with no completion arc, the MSHR never",
          "retires, and the cache waits forever for acks it has",
          "already had.",
          "",
          "Caught by: test_stress racing configuration -> B20"]),
    ]
    for x, title, col, body in panels:
        s.rect(x, 590, 540, 208, fill="#ffffff", stroke=col, rx=8, sw=1.6)
        s.text(x + 20, 616, title, size=12, anchor="start", weight="700",
               fill=col)
        s.lines(x + 20, 638, body, size=10, anchor="start", fill=INK,
                mono=False, gap=15)

    s.note(60, 826, [
        "Two details that are not arbitrary. The condition is VN1 *valid*, not VN1 *taken*: a forward can sit "
        "at the input for several cycles while the handler",
        "finishes an earlier one, and S1 touching the line during that window is the same bug with a wider gap "
        "— and using `taken` would close a combinational",
        "loop, since it depends on retirement, which depends on S1. And it cannot livelock: a forward is taken "
        "in bounded time, after which the conflict clears.",
    ])
    return s.write(path)


# ------------------------------------------------------------- directory ----
def dir_uarch(path):
    s = Svg(1240, 1000,
            "Directory controller — one request at a time, and what that buys",
            "The L2 bank and the directory are the same storage: the "
            "coherence metadata lives beside the tag. One transaction is in "
            "flight; everything it waits on gets a TBE.")

    s.rect(60, 128, 230, 84, fill="#ffffff", stroke=V0, rx=8, sw=1.8)
    s.text(175, 154, "VN0 in — requests", size=12, weight="700", fill=V0)
    s.lines(175, 172, ["GetS GetM PutS PutM PutE",
                       "MAY STALL: the head is not",
                       "popped until it commits"], size=9.5, fill=MUTED,
            mono=False, gap=13)

    s.rect(60, 236, 230, 84, fill="#ffffff", stroke=V2, rx=8, sw=1.8)
    s.text(175, 262, "VN2 in — responses", size=12, weight="700", fill=V2)
    s.lines(175, 280, ["WB-Data, Inv-Ack, Recall-Ack",
                       "TRUE SINK: buffered, and an",
                       "assertion says it never fills"], size=9.5, fill=MUTED,
            mono=False, gap=13)

    s.group_box(330, 110, 600, 250, "dir_ctrl", role="control")
    s.text(630, 154, "one transaction in flight — 12 controller states",
           size=11, fill=MUTED, mono=False)

    def pills(y, names, col, label):
        s.text(342, y - 8, label, size=10, anchor="start", fill=col,
               weight="650")
        n = len(names)
        w = (600 - 24 - (n - 1) * 6) / n
        for i, nm in enumerate(names):
            x = 342 + i * (w + 6)
            s.rect(x, y, w, 30, fill="#ffffff", stroke=col, rx=15, sw=1.4)
            size = min(9.5, (w - 8) / (0.62 * len(nm)))
            s.text(x + w / 2, y + 19, nm, size=size, weight="600", fill=col)
            if i:
                s.raw(f'<path d="M {x - 6},{y + 15} L {x},{y + 15}" '
                      f'stroke="{col}" stroke-width="1.4" fill="none"/>')

    pills(196, ["IDLE", "LOOK", "MEM_REQ", "MEM_WAIT", "EXEC", "WRDATA",
                "SEND"], INK, "serving a request")
    pills(268, ["BINV", "BSEND", "BWB", "MEM_WAIT_WB", "BFREE"], "#c2565c",
          "freeing an L2 way — the recall path (inclusion)")
    s.text(630, 330, "a request that finds the line in S_D is left at the head "
           "and retried, not queued behind", size=10, fill=MUTED, mono=False)
    s.text(630, 346, "— blanket head-of-line blocking, which decision D13 "
           "argues is safe, and measures what it costs", size=10, fill=MUTED,
           mono=False)

    s.arrow([(290, 170), (330, 170)], color=V0, sw=2.0)
    s.arrow([(290, 278), (330, 278)], color=V2, sw=2.0)

    s.rect(970, 128, 210, 84, fill="#ffffff", stroke=V1, rx=8, sw=1.8)
    s.text(1075, 154, "VN1 out", size=12, weight="700", fill=V1)
    s.lines(1075, 172, ["Fwd-GetS, Fwd-GetM, Inv,", "Put-Ack, Recall,",
                        "Recall-Inv"], size=9.5, fill=MUTED, mono=False,
            gap=13)
    s.rect(970, 236, 210, 84, fill="#ffffff", stroke=V2, rx=8, sw=1.8)
    s.text(1075, 262, "VN2 out", size=12, weight="700", fill=V2)
    s.lines(1075, 280, ["Data + AckCount", "(and Data-E when this", 
                        "tile may go exclusive)"], size=9.5, fill=MUTED,
            mono=False, gap=13)
    s.arrow([(930, 170), (970, 170)], color=V1, sw=2.0)
    s.arrow([(930, 278), (970, 278)], color=V2, sw=2.0)

    # ---- storage ------------------------------------------------------------
    s.block(330, 400, 280, 66, "l2_bank", role="storage", size=13,
            sub=("16 KB — 64 sets x 8 ways",))
    s.block(630, 400, 300, 66, "directory metadata", role="storage", size=13,
            sub=("dir_state, owner, sharer vector, data_valid",))
    s.block(970, 400, 210, 66, "tbe_file", role="storage", size=13,
            sub=("4 entries, ack counter",))
    for x in (470, 780, 1075):
        s.arrow([(x, 360), (x, 400)], color=MUTED, sw=1.6, both=True)
    s.text(630, 486, "the tag and the coherence metadata are one array entry, "
           "read and written together", size=10, fill=MUTED, mono=False)

    s.block(60, 400, 230, 66, "memory channel", role="external", size=12,
            sub=("line read / line write-back",))
    s.arrow([(290, 433), (330, 433)], color=INK, sw=1.6, both=True)

    # ---- inclusion / recall -------------------------------------------------
    s.rect(60, 520, 1120, 214, fill="#fdf6f6", stroke="#c2565c", rx=8)
    s.text(84, 548, "Strict inclusion, and why a recall does not need new "
           "protocol arcs", size=13, anchor="start", weight="700",
           fill="#8d3b40")
    s.note(84, 570, [
        "The L2 is strictly inclusive, so freeing a way means first removing the line from every L1 that "
        "holds it. That is what D_BINV .. D_BFREE does.",
    ])
    steps = [
        (84, "victim in S", V1, ["Inv to each sharer", "-> Inv-Ack on VN2",
                                 "", "reuses the arc an ordinary",
                                 "GetM already needs"]),
        (360, "victim in E", V1, ["Recall to the owner",
                                  "-> Recall-Ack on VN2", "",
                                  "clean, so no data comes back"]),
        (636, "victim in M", V1, ["Recall to the owner",
                                  "-> WB-Data on VN2", "",
                                  "the dirty line goes to memory",
                                  "before the way is released"]),
        (912, "why new message types", "#8d3b40",
         ["A recall's ack is consumed by the", "DIRECTORY; an ordinary Inv-Ack",
          "by the requesting L1. One VN2 type", "cannot have two consumers,",
          "so Recall / Recall-Inv / Recall-Ack", "exist (D18, bug B15)."]),
    ]
    for x, title, col, body in steps:
        s.rect(x, 598, 244, 120, fill="#ffffff", stroke=col, rx=7, sw=1.5)
        s.text(x + 14, 620, title, size=11, anchor="start", weight="700",
               fill=col)
        s.lines(x + 14, 638, body, size=9.5, anchor="start", fill=MUTED,
                mono=False, gap=13)

    s.rect(60, 756, 545, 164, fill="#f7f9fb", stroke=RULE, rx=8)
    s.text(84, 782, "Everything the directory waits on gets a TBE",
           size=12, anchor="start", weight="650")
    s.note(84, 802, [
        "Including a line in S_D, whose state is already in the",
        "metadata and does not need one. The TBE is not there to",
        "hold state — it is there so the liveness bound is a single",
        "assertion over a single structure, rather than a timer per",
        "line. An entry that ages past TBE_TIMEOUT is a deadlock,",
        "and the assertion says which line and which state it is in.",
    ])

    s.rect(635, 756, 545, 164, fill="#f7f9fb", stroke=RULE, rx=8)
    s.text(659, 782, "Five directory states, and the one that is not obvious",
           size=12, anchor="start", weight="650")
    s.note(659, 802, [
        "I, S, E, M and S_D. S_D is the transient a line enters when",
        "a GetS finds it in M: the owner has been forwarded the",
        "request and owes the directory a copy, so the directory",
        "knows the sharer set but not yet the data. A second GetS",
        "arriving in S_D must NOT be served from stale memory —",
        "race R7, and the mutation that serves it is killed by a test.",
    ])
    return s.write(path)


# ---------------------------------------------------------------- router ----
def router_uarch(path):
    s = Svg(1240, 900, "Router — three stages, five ports, six VCs per port",
            "Input-buffered, virtual-channel, credit flow control, XY routing. "
            "All outputs registered, so the link is a wire and nothing else.")

    stages = [(60, 300, "BW + RC", "buffer write, route compute"),
              (392, 300, "VA + SA", "VC allocate, switch allocate"),
              (724, 220, "ST", "crossbar traversal, registered out"),
              (976, 204, "LT", "link traversal — the wire")]
    for x, w, name, sub in stages:
        s.rect(x, 104, w, 34, fill="#eef1f4", stroke=RULE, rx=8, sw=1.2)
        s.text(x + w / 2, 126, name, size=12, weight="700")
        s.text(x + w / 2, 156, sub, size=10, fill=MUTED, mono=False)

    # ---- input units --------------------------------------------------------
    s.group_box(60, 176, 300, 300, "input_unit  x5  (N E S W Local)",
                role="storage")
    for i, (vn, lab) in enumerate(((0, "VN0 vc0"), (0, "VN0 vc1"),
                                   (1, "VN1 vc0"), (1, "VN1 vc1"),
                                   (2, "VN2 vc0"), (2, "VN2 vc1"))):
        col = VNET[vn][0]
        y = 206 + i * 34
        s.rect(80, y, 150, 28, fill="#ffffff", stroke=col, rx=5, sw=1.4)
        s.text(155, y + 19, lab, size=10.5, weight="600", fill=col)
        for k in range(4):
            s.rect(240 + k * 22, y + 4, 18, 20, fill="#ffffff", stroke=RULE,
                   rx=3, sw=1.0)
    s.text(264, 424, "VC_DEPTH = 4 flits", size=9.5, fill=MUTED, mono=False)
    s.text(210, 452, "route_compute: XY, purely combinational from the head",
           size=9.5, fill=MUTED, mono=False)

    # ---- allocators ---------------------------------------------------------
    s.block(392, 190, 300, 108, "vc_allocator", role="control", size=13,
            label_dy=30,
            sub=("picks an output VC for each head flit",
                 "VC index = f(source tile), never 'any free one'",
                 "so a pair of tiles keeps one FIFO subnetwork"))
    s.block(392, 318, 300, 100, "switch_allocator", role="control", size=13,
            label_dy=28,
            sub=("per-flit, separable input-first then",
                 "output-first round robin — one winner",
                 "per input port and per output port"))
    s.arrow([(360, 250), (392, 250)], color=INK, sw=1.8)
    s.arrow([(360, 366), (392, 366)], color=INK, sw=1.8)
    s.arrow([(542, 298), (542, 318)], color=INK, sw=1.6)

    # ---- crossbar and output ------------------------------------------------
    s.block(724, 190, 220, 228, "crossbar", role="datapath", size=14,
            label_dy=110, sub=("5 x 5, one flit per", "output port per cycle"))
    s.arrow([(692, 250), (724, 250)], color=INK, sw=1.8)
    s.arrow([(692, 366), (724, 366)], color=INK, sw=1.8)

    for i, p in enumerate(("N", "E", "S", "W", "L")):
        y = 200 + i * 44
        s.rect(976, y, 96, 34, fill="#e4ecf7", stroke="#5b86c4", rx=5, sw=1.4)
        s.text(1024, y + 22, f"reg {p}", size=11, weight="600")
        s.arrow([(944, y + 17), (976, y + 17)], color=INK, sw=1.4)
        s.arrow([(1072, y + 17), (1172, y + 17)], color=INK, sw=1.6)

    # ---- credits ------------------------------------------------------------
    s.rect(392, 444, 552, 56, fill="#ffffff", stroke="#5a9e68", rx=8, sw=1.6)
    s.text(668, 468, "credit_counter — one per output VC", size=12,
           weight="700", fill="#5a9e68")
    s.text(668, 486, "a flit sent spends a credit; the downstream returns it "
           "when that flit leaves its buffer", size=10, fill=MUTED, mono=False)
    s.arrow([(1172, 440), (944, 440), (944, 472)], color="#5a9e68", sw=1.6)
    s.text(1100, 432, "credits back", size=10, fill="#5a9e68", mono=False)

    # ---- the two rules ------------------------------------------------------
    s.rect(60, 536, 560, 170, fill="#f7f9fb", stroke="#5b86c4", rx=8)
    s.text(84, 562, "An output VC is released on the TAIL's CREDIT  (D9)",
           size=12, anchor="start", weight="700", fill="#2f5c93")
    s.note(84, 582, [
        "Not when this router sends the tail. That is one credit round",
        "trip of extra occupancy per VC, and it is what guarantees a head",
        "flit never arrives at a downstream VC that is still draining the",
        "previous packet. With two VCs per vnet the sibling covers the gap,",
        "so the throughput cost is small and the correctness is absolute.",
        "",
        "Measured: no change in saturation throughput, ~1 cycle of latency.",
    ])

    s.rect(640, 536, 540, 170, fill="#fdf6f6", stroke="#c2565c", rx=8)
    s.text(664, 562, "Arbitration is per FLIT, not per packet",
           size=12, anchor="start", weight="700", fill="#8d3b40")
    s.note(664, 582, [
        "Wormhole routing requires a packet's flits to be contiguous on",
        "its VIRTUAL CHANNEL, not on the physical port. Two packets on",
        "different VCs may interleave on the wire; two packets on the same",
        "VC may not, and VC allocation never lets that happen.",
        "",
        "The router test checks the stronger property: split each output",
        "VC's flit stream at tail boundaries and every piece is one packet.",
    ])

    s.note(60, 742, [
        "Sizing, and why each number is what it is.  5 ports: four neighbours and the local tile.  "
        "6 VCs per port: three virtual networks x 2, and the",
        "second VC per vnet exists so a blocked packet does not idle the vnet — one per vnet is correct but "
        "leaves measurable throughput on the table.",
        "4 flits per VC buffer: the longest packet is 3 flits (head + 2 body) and the fourth slot covers the "
        "credit round trip.  Occupancy 4 is therefore",
        "unreachable by construction, which is one of the coverage exclusions with a written argument rather "
        "than a waiver.",
    ])
    return s.write(path)


if __name__ == "__main__":
    for fn, name in ((tile, "tile_uarch"), (l1_pipeline, "l1_pipeline"),
                     (l1_state_writers, "l1_state_writers"),
                     (dir_uarch, "dir_uarch"),
                     (router_uarch, "router_uarch")):
        print(fn(os.path.join(OUT, name + ".svg")))
