"""System-level diagrams: topology, address decode, packet format, vnets."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from svg import Svg, VNET, INK, MUTED, RULE, ROLE
import params

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "img")

# Every number in these figures comes from the package, not from memory.
P = params.load()


def kb(sets, ways):
    b = sets * ways * P.LINE_BYTES
    return f"{b // 1024} KB" if b % 1024 == 0 else f"{b} B"


def rng(lsb, w):
    return f"[{lsb + w - 1}:{lsb}]" if w > 1 else f"[{lsb}]"


# ---------------------------------------------------------------- topology --
def topology(path):
    TW, TH = 340, 232
    pos = {0: (60, 96), 1: (580, 96), 2: (60, 392), 3: (580, 392)}
    s = Svg(980, 812, "System topology — four tiles on a 2x2 mesh",
            "Every tile is identical: a cache, a directory bank for the quarter "
            "of the address space it homes, its own memory, and a router.")

    for n, (tx, ty) in pos.items():
        x, y = tx + 0, ty + 0
        s.rect(x, y, TW, TH, fill="#fbfcfd", stroke=RULE, rx=10, sw=1.6)
        s.text(x + 14, y + 22, f"Tile {n}", size=13, anchor="start",
               weight="700")
        s.text(x + TW - 14, y + 22, f"(x={n & 1}, y={(n >> 1) & 1})", size=11,
               anchor="end", fill=MUTED)

        s.block(x + 16, y + 34, 150, 30, "core port", role="external", size=11)
        s.block(x + 16, y + 72, 150, 56, f"L1D  {kb(P.L1_SETS, P.L1_WAYS)}",
                role="storage", size=12,
                sub=(f"{P.L1_SETS} sets x {P.L1_WAYS} ways",
                     f"MSHR x{P.MSHR_ENTRIES}, 13 states"))
        s.block(x + 174, y + 34, 150, 56,
                f"L2 bank  {kb(P.L2_SETS, P.L2_WAYS)}", role="storage",
                size=12, sub=(f"{P.L2_SETS} sets x {P.L2_WAYS} ways",
                              f"directory + TBE x{P.TBE_ENTRIES}"))
        s.block(x + 174, y + 98, 150, 30, "memory", role="external", size=11)
        s.block(x + 16, y + 138, 308, 32, "tile_nic", role="iface", size=12,
                sub=())
        s.text(x + TW / 2, y + 168,
               f"packetise / reassemble, {P.NUM_VNETS} separate vnets",
               size=9.5, fill=MUTED)
        s.block(x + 16, y + 182, 308, 34, "router", role="control", size=12)
        s.text(x + TW / 2, y + 211,
               f"{P.NUM_PORTS} ports x {P.VCS_PER_PORT} VCs, XY, "
               f"credit flow control", size=9.5, fill=MUTED)

    # inter-router links, drawn between tile boundaries
    for (a, b, pts, lab) in [
        (0, 1, [(400, 295), (580, 295)], "E / W"),
        (2, 3, [(400, 591), (580, 591)], "E / W"),
        (0, 2, [(230, 328), (230, 392)], "S / N"),
        (1, 3, [(750, 328), (750, 392)], "S / N"),
    ]:
        s.arrow(pts, color=INK, sw=2.2, both=True)
        mx = (pts[0][0] + pts[1][0]) / 2
        my = (pts[0][1] + pts[1][1]) / 2
        if pts[0][1] == pts[1][1]:
            s.text(mx, my + 22, lab + "   flit + credit", size=9.5, fill=MUTED)
        else:
            s.text(mx + 62, my + 4, lab, size=10, fill=MUTED)

    # XY routing example, drawn ON the links it actually uses
    c = "#c2565c"
    s.raw(f'<path d="M 596,289 L 404,289" fill="none" stroke="{c}" '
          f'stroke-width="3" stroke-dasharray="8 4" '
          f'marker-end="url(#{s.marker(c)})"/>')
    s.raw(f'<path d="M 224,328 L 224,388" fill="none" stroke="{c}" '
          f'stroke-width="3" stroke-dasharray="8 4" '
          f'marker-end="url(#{s.marker(c)})"/>')
    s.text(490, 172, "XY routing", size=12.5, fill=c, weight="700")
    s.text(490, 190, "X first, then Y", size=10.5, fill=c)
    s.text(490, 218, "tile 1 -> tile 2", size=10.5, fill=c)
    s.text(490, 232, "goes 1 -> 0 -> 2,", size=10.5, fill=c)
    s.text(490, 246, "never 1 -> 3 -> 2", size=10.5, fill=c)
    s.text(490, 352, "(1) X hop", size=10, fill=c)
    s.text(490, 366, "(2) then Y hop", size=10, fill=c)

    s.text(490, 668,
           f"home bank = addr{rng(P.BANK_LSB, P.BANK_W)}", size=13,
           weight="650")
    s.text(490, 688,
           "consecutive lines interleave across banks, so most requests "
           "cross the network", size=11, fill=MUTED, mono=False)

    s.legend(60, 726, [
        (ROLE["storage"][1], "storage: caches, buffers, queues"),
        (ROLE["control"][1], "control: FSMs, tables, allocators"),
        (ROLE["iface"][1], "interface: packetisation boundary"),
        (ROLE["external"][1], "external: core port, memory model"),
    ], cols=2, mono=False)
    s.note(600, 726, [
        "Mean hops 1.333 (uniform, no self).  Diameter 2 hops.",
        "Zero-load latency 8.1 cycles; knee at ~0.60 flits/cycle/node.",
        "One path per pair, so adaptive routing has nothing to adapt to.",
        "No X turn ever follows a Y turn, so the channel graph is acyclic.",
    ])
    return s.write(path)


# ----------------------------------------------------------- address decode --
def address(path):
    s = Svg(980, 500, "Address decode, and the tag that is not contiguous",
            "The home-bank bits sit BELOW the index field. That is the whole "
            "reason the L1 tag has a hole in it.")
    X0, W = 176, 560
    fields = [("upper tag", P.L1_UTAG_W), ("set index", P.L1_IDX_W),
              ("bank", P.BANK_W), ("offset", P.OFFSET_W)]
    y = 104
    geom = s.bitfield(X0, y, W, 54, fields,
                      role_of=lambda n: "accent" if n == "bank" else "plain")
    pos = {n: (x, w) for n, _, x, w in geom}
    lsb = P.ADDR_W
    for n, w in fields:
        lsb -= w
        x, fw = pos[n]
        s.text(x + fw / 2, y - 12, rng(lsb, w), size=10.5, fill=MUTED)
    s.text(X0 - 14, y + 32, f"addr[{P.ADDR_W - 1}:0]", size=11.5, anchor="end",
           fill=INK, weight="600")
    s.text(X0, y + 74, str(P.ADDR_W - 1), size=10, anchor="start", fill=MUTED)
    s.text(X0 + W, y + 74, "0", size=10, anchor="end", fill=MUTED)

    def bracket(names, yy, label, color, note):
        for n in names:
            gx, gw = pos[n]
            s.rect(gx, yy, gw, 28, fill="none", stroke=color, rx=5, sw=2.2)
        s.text(X0 - 14, yy + 19, label, size=11.5, anchor="end", fill=color,
               weight="650")
        s.text(X0 + W + 14, yy + 19, note, size=10.5, anchor="start",
               fill=MUTED, mono=False)

    bracket(["upper tag"], 200, f"L2 tag  {P.L2_TAG_W}b", "#5a9e68",
            "bank is implied")
    bracket(["set index"], 242, f"index  {P.L1_IDX_W}b", "#5b86c4",
            "L1 and L2 alike")
    bracket(["upper tag", "bank"], 284, f"L1 tag  {P.L1_TAG_W}b", "#c2565c",
            "two pieces")
    utag_lsb = P.IDX_LSB + P.L1_IDX_W
    s.text(X0 + W / 2, 336,
           f"L1 tag = {{ addr{rng(utag_lsb, P.L1_UTAG_W)}, "
           f"addr{rng(P.BANK_LSB, P.BANK_W)} }}  --  NON-CONTIGUOUS",
           size=12, fill="#c2565c", weight="700")

    s.rect(60, 358, 860, 112, fill="#fbe6e6", stroke="#c2565c", rx=8)
    s.text(84, 384, "Why the hole is load-bearing", size=12.5, anchor="start",
           weight="700", fill="#8d3b40")
    s.note(84, 404, [
        "Every L1 caches lines from all four banks, so the bank bits are not implied by the cache instance --",
        "and they sit below the index, so the index does not cover them either. Leave them out of the tag and",
        "two lines whose addresses differ only in home bank land in the same L1 set with the same tag, and",
        "alias onto each other. That is silent data corruption, and it is the first thing in coh_pkg.sv.",
    ])
    return s.write(path)


# ------------------------------------------------------------- flit format --
def flit(path):
    s = Svg(980, 560, "Packet and flit format",
            f"Wormhole with virtual channels: a control message is one "
            f"head+tail flit, a data message is head plus "
            f"{params.spell(P.FLITS_PER_LINE)} body flits.")
    X0, W = 60, 860

    def row(y, title, sub, fields, role_of, min_w=34):
        s.text(X0, y - 14, title, size=12.5, anchor="start", weight="650")
        if sub:
            s.text(X0 + W, y - 14, sub, size=10.5, anchor="end", fill=MUTED,
                   mono=False)
        return s.bitfield(X0, y, W, 48, fields, role_of=role_of, min_w=min_w)

    ctrl = ("head", "tail", "vnet", "vc_id")
    rt = ("dst_x", "dst_y", "src_id")
    flit_fields = params.struct(P, "flit_t")
    flit_bits = sum(w for _, w in flit_fields)
    row(100, f"flit_t  —  {flit_bits} bits, the unit on every link",
        "one flit per cycle per port", flit_fields,
        lambda n: "control" if n in ctrl else ("iface" if n in rt
                                               else "datapath"))

    s.arrow([(560, 152), (560, 212)], color=MUTED, sw=1.4, dash="4 3")
    s.text(548, 186, "on a HEAD flit the payload carries:", size=10.5,
           anchor="end", fill=MUTED, mono=False)

    head_fields = params.struct(P, "head_payload_t")
    head_bits = sum(w for _, w in head_fields)
    pad = P.FLIT_PAYLOAD_W - head_bits
    msg_w = dict(head_fields)["msg_type"]
    row(232, f"head_payload_t  —  {head_bits} of the "
        f"{P.FLIT_PAYLOAD_W} payload bits",
        f"{pad} bits spare, which is where the {msg_w}-bit msg_type came from",
        head_fields + [("unused", pad)],
        lambda n: "external" if n == "unused" else "control",
        min_w=84)

    row(346, f"body flit  —  payload is line data, "
        f"{params.spell(P.FLITS_PER_LINE)} per "
        f"{P.LINE_W}-bit line", "",
        [(f"data[{P.FLIT_PAYLOAD_W - 1}:0]", P.FLIT_PAYLOAD_W)],
        lambda n: "datapath")

    s.rect(60, 422, 420, 116, fill="#f7f9fb", stroke=RULE, rx=8)
    s.text(82, 446, "Packet lengths", size=12, anchor="start", weight="650")
    for i, (a, b) in enumerate([
            ("control  GetS, Inv, Put-Ack, ...", "1 flit   head and tail in one"),
            ("data     Data, WB-Data, PutM, ...",
             f"{1 + P.FLITS_PER_LINE} flits  head + {P.FLITS_PER_LINE} body")]):
        s.text(82, 468 + i * 17, a, size=10, anchor="start", fill=INK)
        s.text(300, 468 + i * 17, b, size=10, anchor="start", fill=MUTED)
    s.text(82, 516, f"So a VC buffer never holds more than "
           f"{params.spell(1 + P.FLITS_PER_LINE)} flits.",
           size=10.5, anchor="start", fill=INK, mono=False)

    s.rect(500, 422, 420, 116, fill="#f7f9fb", stroke=RULE, rx=8)
    s.text(522, 446, f"Why VC_DEPTH is {P.VC_DEPTH}, and why occupancy "
           f"{P.VC_DEPTH} never happens",
           size=11.5, anchor="start", weight="650")
    s.note(522, 466, [
        "A VC carries one packet at a time and is not released until",
        f"its tail's credit returns. The longest packet is "
        f"{params.spell(1 + P.FLITS_PER_LINE)} flits,",
        f"so {params.spell(1 + P.FLITS_PER_LINE)} is the deepest a buffer "
        f"gets. The last slot is there",
        "so a credit round trip does not stall a full channel.",
    ])
    return s.write(path)


if __name__ == "__main__":
    for f, name in ((topology, "topology"), (address, "address_decode"),
                    (flit, "flit_format")):
        print(f(os.path.join(OUT, name + ".svg")))
