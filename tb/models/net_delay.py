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

Each element holds ONE message and backpressures its source behind it, so
per-source ordering is preserved: the holds change *when* a message is
delivered relative to other sources, never the order within one source.
"""

NUM_TILES = 4


class NetDelay:
    def __init__(self, dut):
        self.dut = dut
        if hasattr(dut, "hold_vn0_i"):
            self.sigs = (dut.hold_vn0_i, dut.hold_vn1_i, dut.hold_vn2_i)
            self.flavour = "system_top (hold_vn*_i)"
        else:
            self.sigs = (dut.delay_vn0_i, dut.delay_vn1_i, dut.delay_vn2_i)
            self.flavour = "direct harness (delay_vn*_i)"
        self.delays = [[0] * NUM_TILES for _ in range(3)]
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
        self.set(2, src, cycles)

    def clear(self):
        self.delays = [[0] * NUM_TILES for _ in range(3)]
        self.apply()

    def apply(self):
        for vnet, sig in enumerate(self.sigs):
            word = 0
            for t in range(NUM_TILES):
                word |= self.delays[vnet][t] << (t * 8)
            sig.value = word

    def describe(self) -> str:
        names = ("VN0", "VN1", "VN2")
        parts = [
            f"{names[v]}[tile {t}]={d}"
            for v in range(3)
            for t, d in enumerate(self.delays[v])
            if d
        ]
        return ", ".join(parts) if parts else "no holds"
