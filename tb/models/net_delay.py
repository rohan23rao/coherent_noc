"""Deterministic per-source, per-virtual-network message holds.

This is the hook the directed race tests drive. Slowing one source's messages
relative to another's forces the exact interleaving a named race requires,
without touching rtl/ and without adding settle cycles anywhere -- a race
reproduced by waiting longer is not reproduced at all.

Two tops carry the hook under different port names: the direct-connect harness
calls them `delay_vn*_i` and the full system calls them `hold_vn*_i`, because
in the full system the element is a genuine pipeline register on the network
interface boundary whose latency happens to be programmable. This class binds
to whichever is present so the scenarios can be written once.

There are FOUR channels per tile, not three. VN2 has two independent senders in
a tile -- the cache and the home bank -- and race R1 is precisely the case
where the bank's Data must arrive after the caches' Inv-Acks, so a single VN2
control cannot express it. `set_vn2` delays the cache's responses and
`set_vn2_dir` the directory's.

Each element holds ONE message and backpressures its source behind it, so
per-source ordering is preserved: the holds change *when* a message is
delivered relative to other sources, never the order within one source.
"""

NUM_TILES = 4
VN0, VN1, VN2_L1, VN2_DIR = 0, 1, 2, 3
NUM_CHANNELS = 4


class NetDelay:
    def __init__(self, dut):
        self.dut = dut
        if hasattr(dut, "hold_vn0_i"):
            self.sigs = (dut.hold_vn0_i, dut.hold_vn1_i, dut.hold_vn2_i,
                         dut.hold_vn2_dir_i)
            self.flavour = "system_top (hold_vn*_i)"
        else:
            self.sigs = (dut.delay_vn0_i, dut.delay_vn1_i, dut.delay_vn2_i,
                         dut.delay_vn2_dir_i)
            self.flavour = "direct harness (delay_vn*_i)"
        self.delays = [[0] * NUM_TILES for _ in range(NUM_CHANNELS)]
        self.apply()

    def set(self, vnet: int, src: int, cycles: int):
        assert 0 <= cycles <= 255, f"hold of {cycles} cycles is out of range"
        self.delays[vnet][src] = cycles
        self.apply()

    def set_vn0(self, src: int, cycles: int):
        self.set(0, src, cycles)

    def set_vn1(self, src: int, cycles: int):
        self.set(1, src, cycles)

    def set_vn2(self, src: int, cycles: int):
        """Delay the CACHE's responses out of tile `src`."""
        self.set(VN2_L1, src, cycles)

    def set_vn2_dir(self, src: int, cycles: int):
        """Delay the home BANK's responses out of tile `src`."""
        self.set(VN2_DIR, src, cycles)

    def clear(self):
        self.delays = [[0] * NUM_TILES for _ in range(NUM_CHANNELS)]
        self.apply()

    def apply(self):
        for vnet, sig in enumerate(self.sigs):
            word = 0
            for t in range(NUM_TILES):
                word |= self.delays[vnet][t] << (t * 8)
            sig.value = word

    def describe(self) -> str:
        names = ("VN0", "VN1", "VN2/L1", "VN2/dir")
        parts = [
            f"{names[v]}[tile {t}]={d}"
            for v in range(NUM_CHANNELS)
            for t, d in enumerate(self.delays[v])
            if d
        ]
        return ", ".join(parts) if parts else "no holds"
