"""Atomic, sequentially-consistent reference memory.

A dict of line address -> 256-bit value. It has no notion of transient states,
coherence, or time: every operation applies instantaneously and in the order it
is retired. That is legitimate because coherence, unlike a memory consistency
model, has a simple sequential specification -- a coherent machine is one whose
per-line access order is *some* total order consistent with each core's program
order, and checking every retired operation against an atomic memory is exactly
that check.

What this does NOT verify, and must not be claimed to: the memory consistency
model. There are no fences, no store buffer and no reordering here, so the
testbench cannot distinguish SC from TSO from anything weaker. See
docs/verification.md for the honest list.
"""

LINE_BYTES = 32
LINE_BITS = LINE_BYTES * 8
WORD_BITS = 32
WORD_BYTES = WORD_BITS // 8
WORDS_PER_LINE = LINE_BITS // WORD_BITS


def line_addr(addr: int) -> int:
    return addr >> 5


def word_sel(addr: int) -> int:
    return (addr >> 2) & (WORDS_PER_LINE - 1)


class GoldenMemory:
    """Flat memory, line granular, zero-initialised to match mem_model."""

    def __init__(self):
        self.lines = {}

    def read_line(self, la: int) -> int:
        return self.lines.get(la, 0)

    def write_line(self, la: int, value: int):
        self.lines[la] = value & ((1 << LINE_BITS) - 1)

    def load(self, addr: int) -> int:
        line = self.read_line(line_addr(addr))
        return (line >> (word_sel(addr) * WORD_BITS)) & ((1 << WORD_BITS) - 1)

    def store(self, addr: int, wdata: int, be: int = 0xF) -> int:
        """Apply a byte-enabled store; returns the word as it reads back."""
        la = line_addr(addr)
        line = self.read_line(la)
        base = word_sel(addr) * WORD_BITS
        for b in range(WORD_BYTES):
            if be & (1 << b):
                shift = base + b * 8
                byte = (wdata >> (b * 8)) & 0xFF
                line = (line & ~(0xFF << shift)) | (byte << shift)
        self.write_line(la, line)
        return (line >> base) & ((1 << WORD_BITS) - 1)

    def snapshot(self) -> dict:
        return dict(self.lines)
