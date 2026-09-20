"""Flit encode/decode matching coh_pkg::flit_t.

A packed struct in SystemVerilog lays its first-declared field in the most
significant bits, so the layout is, from the top:

    head(1) tail(1) vnet(2) vc_id(1) dst_x(1) dst_y(1) src_id(2) payload(128)

and a packed array ``flit_t [N-1:0]`` places index 0 in the least significant
slice. Both of those are verified by test_noc_router.py::test_flit_codec
against the DUT rather than taken on faith -- getting either backwards produces
a testbench that "works" on symmetric values and fails mysteriously on real
traffic.
"""

from dataclasses import dataclass

HEAD_W = 1
TAIL_W = 1
VNET_W = 2
VC_ID_W = 1
DST_X_W = 1
DST_Y_W = 1
SRC_ID_W = 2
PAYLOAD_W = 128

FLIT_W = HEAD_W + TAIL_W + VNET_W + VC_ID_W + DST_X_W + DST_Y_W + SRC_ID_W + PAYLOAD_W

# Field offsets from the LSB, built from the declaration order above.
_FIELDS = [
    ("payload", PAYLOAD_W),
    ("src_id", SRC_ID_W),
    ("dst_y", DST_Y_W),
    ("dst_x", DST_X_W),
    ("vc_id", VC_ID_W),
    ("vnet", VNET_W),
    ("tail", TAIL_W),
    ("head", HEAD_W),
]

_OFFSETS = {}
_off = 0
for _name, _w in _FIELDS:
    _OFFSETS[_name] = (_off, _w)
    _off += _w
assert _off == FLIT_W, f"field widths sum to {_off}, expected {FLIT_W}"


@dataclass
class Flit:
    head: int = 0
    tail: int = 0
    vnet: int = 0
    vc_id: int = 0
    dst_x: int = 0
    dst_y: int = 0
    src_id: int = 0
    payload: int = 0

    def pack(self) -> int:
        word = 0
        for name, (off, width) in _OFFSETS.items():
            value = getattr(self, name)
            assert 0 <= value < (1 << width), f"{name}={value} does not fit in {width} bits"
            word |= value << off
        return word

    @classmethod
    def unpack(cls, word: int) -> "Flit":
        kwargs = {}
        for name, (off, width) in _OFFSETS.items():
            kwargs[name] = (word >> off) & ((1 << width) - 1)
        return cls(**kwargs)

    def same_packet_as(self, other: "Flit") -> bool:
        return (self.vnet, self.vc_id, self.src_id) == (other.vnet, other.vc_id, other.src_id)


def pack_ports(flits: list) -> int:
    """Pack a per-port list into a `flit_t [N-1:0]` vector; index 0 is the LSB."""
    word = 0
    for idx, flit in enumerate(flits):
        word |= flit.pack() << (idx * FLIT_W)
    return word


def unpack_port(word: int, idx: int) -> Flit:
    """Extract port `idx` from a packed `flit_t [N-1:0]` vector."""
    return Flit.unpack((word >> (idx * FLIT_W)) & ((1 << FLIT_W) - 1))
