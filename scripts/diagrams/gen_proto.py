"""Protocol-level diagrams: the virtual networks, and point-to-point ordering."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from svg import Svg, VNET, INK, MUTED, RULE, ROLE

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "img")
V0, V1, V2 = VNET[0][0], VNET[1][0], VNET[2][0]


# ------------------------------------------------------------------ vnets ----
def vnets(path):
    s = Svg(1240, 900,
            "Three virtual networks — the message dependency graph",
            "A request may cause a forward. A forward may cause a response. "
            "A response causes nothing. Three levels of dependency, three "
            "virtual networks.")

    bands = [
        (120, V0, "VN0   request", "L1 -> directory,  directory -> memory",
         "GetS   GetM   PutS   PutM   PutE",
         "MAY STALL, for as long as it likes"),
        (268, V1, "VN1   forward", "directory -> L1",
         "Fwd-GetS   Fwd-GetM   Inv   Put-Ack   Recall   Recall-Inv",
         "may stall only on a transient state, and only briefly"),
        (416, V2, "VN2   response",
         "L1 -> L1,  L1 -> directory,  directory -> L1,  memory -> directory",
         "Data   Data-E   Inv-Ack   WB-Data   Recall-Ack",
         "MUST NEVER STALL — this is the sink"),
    ]
    for y, col, name, who, msgs, rule in bands:
        s.rect(60, y, 640, 110, fill="#ffffff", stroke=col, rx=10, sw=2.0)
        s.rect(60, y, 9, 110, fill=col, stroke=col, rx=4)
        s.text(90, y + 30, name, size=15, anchor="start", weight="700",
               fill=col)
        s.text(680, y + 30, who, size=10, anchor="end", fill=MUTED, mono=False)
        s.text(90, y + 58, msgs, size=11, anchor="start", fill=INK)
        s.text(90, y + 84, rule, size=10.5, anchor="start", fill=col,
               mono=False, weight="600")

    s.arrow([(380, 230), (380, 268)], color=INK, sw=2.4)
    s.text(396, 254, "may cause", size=10.5, anchor="start", fill=INK,
           mono=False)
    s.arrow([(380, 378), (380, 416)], color=INK, sw=2.4)
    s.text(396, 402, "may cause", size=10.5, anchor="start", fill=INK,
           mono=False)
    s.arrow([(380, 526), (380, 560)], color="#5a9e68", sw=2.4)
    s.text(396, 552, "causes nothing — the chain terminates", size=11,
           anchor="start", fill="#5a9e68", mono=False, weight="600")

    s.text(380, 592, "a strict partial order, so with one buffer pool per "
           "class no cycle can form", size=12, fill=INK, mono=False,
           weight="650")

    # ---- the sink requirement ----------------------------------------------
    s.rect(740, 120, 440, 226, fill="#eef4fa", stroke=V2, rx=10, sw=1.8)
    s.text(764, 148, "The sink requirement", size=13, anchor="start",
           weight="700", fill="#1f5a86")
    s.note(764, 170, [
        "The argument holds only if the last level is a TRUE sink:",
        "a VN2 message must always be accepted by its destination",
        "in bounded time, without the destination needing to send",
        "anything first.",
        "",
        "It is met by construction, not by hope. The MSHR or TBE",
        "that will consume a response is allocated BEFORE the",
        "request that produces it is sent, so the buffer space is",
        "already reserved. An L1's vn2_ready_o is therefore tied",
        "high, and the directory's VN2 input is a queue whose",
        "fullness is asserted never to occur.",
    ])

    s.rect(740, 366, 440, 226, fill="#ffffff", stroke=RULE, rx=10, sw=1.4)
    s.text(764, 394, "Where each edge is guarded", size=13, anchor="start",
           weight="700")
    guards = [
        ("VN2 must be accepted", "a_vn2_never_backs_up"),
        ("VN2 always has a home", "a_vn2_always_lands"),
        ("VN0 may stall, not forever", "a_tbe_liveness"),
        ("a packet keeps its vnet", "a_vnet_assignment"),
        ("a VC carries one vnet", "a_same_vnet"),
        ("a packet keeps its VC", "a_same_vc"),
        ("credits are never lost", "a_tail_credit_not_lost"),
        ("an event is legal in its state", "a_no_illegal_event"),
    ]
    for i, (what, assertion) in enumerate(guards):
        y = 418 + i * 21
        s.text(764, y, what, size=10, anchor="start", fill=MUTED, mono=False)
        s.text(1156, y, assertion, size=10, anchor="end", fill=INK)

    # ---- the three bugs -----------------------------------------------------
    s.rect(60, 626, 1120, 190, fill="#fdf6f6", stroke="#c2565c", rx=10)
    s.text(84, 654, "Three separate queues are not three separate networks",
           size=13.5, anchor="start", weight="700", fill="#8d3b40")
    s.note(84, 676, [
        "...if anything downstream arbitrates between them without knowing why they are separate. "
        "Three Phase-8 bugs were all that same mistake, and each one deadlocked:",
    ])
    bugs = [
        (84, "B11", "the NIC ejected one message per cycle across all VCs, "
         "lowest index first. A stalled VN0 slot permanently outranked the "
         "VN1 and VN2 slots behind it."),
        (84, "B14", "the VC allocator picked one VC per input port BEFORE "
         "checking output availability, so a blocked VN0 VC won every cycle "
         "and never advanced the round-robin pointer."),
        (84, "B10", "the NIC gave its L1 strict priority over its directory "
         "on VN2, starving one response source."),
    ]
    for i, (x, tag, text) in enumerate(bugs):
        y = 710 + i * 28
        s.text(x, y, tag, size=11, anchor="start", weight="700",
               fill="#8d3b40")
        s.text(x + 44, y, text, size=10.5, anchor="start", fill=INK,
               mono=False)
    s.text(84, 800, "Every one of them passed the direct-connect tests, where "
           "the shared resource was never contended.", size=10.5,
           anchor="start", fill="#8d3b40", mono=False, italic=False)

    s.note(60, 852, [
        "Two VCs per vnet are NOT part of this argument. They are head-of-line-blocking relief; "
        "deleting one would cost throughput and nothing else.",
        "What they ARE part of is ordering — see the next diagram.",
    ])
    return s.write(path)


# ----------------------------------------------------------- VC ordering -----
def vc_ordering(path):
    s = Svg(1240, 840,
            "Point-to-point ordering on VN1 — the property the protocol "
            "assumed and the network did not have",
            "One sender, one receiver, one virtual network, two messages — "
            "and the second one was delivered first.  (bug B19, decision D22)")

    def panel(x, title, col, vcs, verdict, verdict_col):
        s.rect(x, 110, 540, 300, fill="#ffffff", stroke=col, rx=10, sw=1.8)
        s.text(x + 24, 138, title, size=13, anchor="start", weight="700",
               fill=col)
        s.block(x + 24, 158, 110, 52, "bank 2", role="datapath", size=12,
                sub=("directory",))
        s.block(x + 406, 158, 110, 52, "tile 2", role="control", size=12,
                sub=("L1, in MI_A",))
        for i, (vc, msgs, note) in enumerate(vcs):
            y = 238 + i * 64
            s.rect(x + 24, y, 492, 52, fill="#fbfcfd", stroke=RULE, rx=7,
                   sw=1.2)
            s.text(x + 40, y + 22, vc, size=11, anchor="start", weight="700",
                   fill=V1)
            s.text(x + 40, y + 40, msgs, size=10, anchor="start", fill=INK)
            s.text(x + 500, y + 31, note, size=9.5, anchor="end", fill=MUTED,
                   mono=False)
        s.arrow([(x + 134, 184), (x + 406, 184)], color=V1, sw=2.0)
        s.text(x + 270, 176, "VN1", size=10, fill=V1)
        s.text(x + 270, 398, verdict, size=11.5, weight="700",
               fill=verdict_col)

    panel(60, "BEFORE — lowest free VC, chosen again at every hop", "#c2565c",
          [("VC 0", "Fwd-GetS(0xa)  sent at 1431",
            "stalls behind an earlier packet"),
           ("VC 1", "Put-Ack(0xa)   sent at 1436", "free channel, no queue")],
          "arrives 1444 Put-Ack, then 1445 Fwd-GetS  —  ILLEGAL", "#c2565c")

    panel(640, "AFTER — VC index = src_vc_id(sending tile), kept for the "
          "whole path", "#5a9e68",
          [("VC 0", "Fwd-GetS(0xa)  then  Put-Ack(0xa)",
            "one channel, so one queue"),
           ("VC 1", "(used by the tiles whose id has bit 0 set)",
            "an independent subnetwork")],
          "arrives Fwd-GetS, then Put-Ack  —  the order they were sent",
          "#2f7a42")

    s.rect(60, 438, 1120, 150, fill="#fdf6f6", stroke="#c2565c", rx=10)
    s.text(84, 466, "Why the protocol needs this, stated plainly", size=13,
           anchor="start", weight="700", fill="#8d3b40")
    s.note(84, 488, [
        "The directory sends a cache a forward, and later, on processing that cache's Put, a Put-Ack. "
        "The forward is legal in MI_A; after the Put-Ack the",
        "cache is in I, where it has neither data to answer with nor a transaction to answer for. The table "
        "cell is blank precisely because a correct directory never",
        "sends a forward after the ack — which is true of the SENDING order and says nothing about the "
        "ARRIVAL order.",
        "",
        "No directed test would have found it: it needs a forward and a Put-Ack in flight to the same cache "
        "at the same moment, which is a coincidence,",
        "not an interleaving anybody would think to arrange. test_stress_16_lines finds it within a few "
        "thousand requests.",
    ])

    s.rect(60, 614, 730, 140, fill="#f7f9fb", stroke=RULE, rx=10)
    s.text(84, 642, "The fix, in one function", size=12.5, anchor="start",
           weight="650")
    s.text(84, 668, "function automatic logic [VC_ID_W-1:0] src_vc_id"
           "(input logic [TILE_ID_W-1:0] t);", size=10.5, anchor="start",
           fill=INK)
    s.text(84, 686, "\u00a0\u00a0return t[VC_ID_W-1:0];", size=10.5,
           anchor="start", fill=INK)
    s.text(84, 704, "endfunction", size=10.5, anchor="start", fill=INK)
    s.note(84, 726, [
        "The NIC injects on src_vc_id(TILE_ID) and waits if it is busy; the VC allocator's candidate is the "
        "input VC's own index,",
        "not the lowest free one. a_same_vc says a grant never moves a packet between channels.",
    ])

    s.rect(810, 614, 370, 140, fill="#f7f9fb", stroke=RULE, rx=10)
    s.text(834, 642, "What it costs  (D22)", size=12.5, anchor="start",
           weight="650")
    s.note(834, 664, [
        "Channel utilisation. A blocked packet now waits",
        "for one specific channel rather than any free one",
        "in its vnet.",
        "",
        "Measured: 10-15% mean latency below the knee,",
        "no change at saturation.",
    ])
    return s.write(path)


if __name__ == "__main__":
    for fn, name in ((vnets, "vnets"), (vc_ordering, "vc_ordering")):
        print(fn(os.path.join(OUT, name + ".svg")))
