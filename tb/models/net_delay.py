"""Deterministic per-source, per-virtual-network message delays.

This is the hook the directed race tests drive. Slowing one source's messages
relative to another's forces the exact interleaving a named race requires,
without touching rtl/ and without adding settle cycles anywhere -- a race
reproduced by waiting longer is not reproduced at all.

Each delay element holds ONE message and backpressures its source behind it, so
per-source ordering is preserved. The delays therefore change *when* a message
is delivered relative to other sources, never the order within one source.
"""

NUM_TILES = 4
VN0, VN1, VN2 = 0, 1, 2


class NetDelay:
    def __init__(self, dut):
        self.dut = dut
        self.delays = {VN0: [0] * NUM_TILES, VN1: [0] * NUM_TILES, VN2: [0] * NUM_TILES}
        self.apply()

    def set(self, vnet: int, src: int, cycles: int):
        assert 0 <= cycles <= 255, f"delay {cycles} out of range"
        self.delays[vnet][src] = cycles
        self.apply()

    def clear(self):
        for v in self.delays:
            self.delays[v] = [0] * NUM_TILES
        self.apply()

    def apply(self):
        for vnet, sig in ((VN0, self.dut.delay_vn0_i),
                          (VN1, self.dut.delay_vn1_i),
                          (VN2, self.dut.delay_vn2_i)):
            word = 0
            for t in range(NUM_TILES):
                word |= self.delays[vnet][t] << (t * 8)
            sig.value = word

    def describe(self) -> str:
        parts = []
        for name, vnet in (("VN0", VN0), ("VN1", VN1), ("VN2", VN2)):
            for t, d in enumerate(self.delays[vnet]):
                if d:
                    parts.append(f"{name}[tile {t}]={d}")
        return ", ".join(parts) if parts else "no delays"
