"""Four-core request driver and scoreboard for the coherence phases.

Per-line exclusivity, and why it is sound: the testbench allows at most one
outstanding operation per *line* across all four cores. Coherence guarantees a
total order per line, and with one operation in flight at a time that order is
exactly the testbench's issue order -- so the golden atomic memory can be
applied in that order and every response checked against it.

What this deliberately does NOT cover is two cores racing on the same line at
once. That is not an oversight: reconstructing the coherence order for
concurrent same-line operations from the outside is guesswork, and guessing is
how a scoreboard starts accepting wrong answers. Same-line races are covered
instead by the directed race tests, which force an exact interleaving through
the network-delay hook and know what the answer must be.

Concurrency across *different* lines is unrestricted, so all four cores are
active and the protocol sees real overlap.

The race tests reach past `plan()` and issue same-line operations directly,
with `expected` set to None so no per-response check is made; see `_collect`.
"""

import random
from collections import deque

from models.golden import GoldenMemory, line_addr
from tbutil import u

NUM_TILES = 4
OP_LD, OP_ST = 0, 1
NUM_TAGS = 16


class MultiCoreDriver:
    def __init__(self, dut, seed=0):
        self.dut = dut
        self.rng = random.Random(seed)
        self.gold = GoldenMemory()
        self.cycle = 0

        self.free_tags = [set(range(NUM_TAGS)) for _ in range(NUM_TILES)]
        self.expected = [dict() for _ in range(NUM_TILES)]
        self.ctx = [dict() for _ in range(NUM_TILES)]
        self.inflight_line = [dict() for _ in range(NUM_TILES)]
        self.busy_lines = set()

        self.pending = [set() for _ in range(NUM_TILES)]
        self.completed = 0
        self.mismatches = []
        self.offer = [None] * NUM_TILES

    def idle(self):
        d = self.dut
        d.core_req_valid_i.value = 0
        d.core_op_i.value = 0
        d.core_addr_i.value = 0
        d.core_wdata_i.value = 0
        d.core_be_i.value = 0
        d.core_tag_i.value = 0

    def _collect(self):
        d = self.dut
        valid = u(d.core_resp_valid_o)
        tags = u(d.core_resp_tag_o)
        data = u(d.core_resp_rdata_o)
        for t in range(NUM_TILES):
            if valid & (1 << t):
                tag = (tags >> (t * 4)) & 0xF
                got = (data >> (t * 32)) & 0xFFFFFFFF
                assert tag in self.pending[t], (
                    f"tile {t} responded with tag {tag} that was never issued"
                )
                self.pending[t].discard(tag)
                want = self.expected[t].pop(tag)
                where = self.ctx[t].pop(tag)
                ln = self.inflight_line[t].pop(tag)
                self.busy_lines.discard(ln)
                self.free_tags[t].add(tag)
                self.completed += 1
                # `want is None` means the testbench deliberately declined to
                # predict this response: the directed race tests issue two
                # same-line operations at once, where the coherence order is
                # the thing under test and guessing it from outside would be
                # exactly the kind of guess that makes a scoreboard start
                # accepting wrong answers. Those tests check the outcome
                # afterwards, from a quiesced machine, instead.
                if want is not None and got != want:
                    self.mismatches.append(
                        f"tile {t} tag {tag}: got {got:#x}, golden says {want:#x} [{where}]"
                    )

    def plan(self, addrs, store_prob=0.45):
        """Choose one new operation per idle core, respecting line exclusivity."""
        for t in range(NUM_TILES):
            if self.offer[t] is not None or not self.free_tags[t]:
                continue
            candidates = [a for a in addrs if line_addr(a) not in self.busy_lines]
            if not candidates:
                continue
            addr = self.rng.choice(candidates)
            ln = line_addr(addr)
            tag = min(self.free_tags[t])
            if self.rng.random() < store_prob:
                wdata = self.rng.randrange(1 << 32)
                be = self.rng.choice([0xF, 0xF, 0x3, 0x1, 0xC])
                want = self.gold.store(addr, wdata, be)
                ctx = f"ST {addr:#x} be={be:#x}"
                op = OP_ST
            else:
                wdata, be = 0, 0xF
                want = self.gold.load(addr)
                ctx = f"LD {addr:#x}"
                op = OP_LD
            self.free_tags[t].discard(tag)
            self.busy_lines.add(ln)
            self.expected[t][tag] = want
            self.ctx[t][tag] = ctx
            self.inflight_line[t][tag] = ln
            self.offer[t] = (tag, op, addr, wdata, be)

    def drive(self):
        d = self.dut
        valid = 0
        op_w = addr_w = wdata_w = be_w = tag_w = 0
        ready = u(d.core_req_ready_o)
        for t in range(NUM_TILES):
            if self.offer[t] is None:
                continue
            tag, op, addr, wdata, be = self.offer[t]
            valid |= 1 << t
            op_w |= op << (t * 2)
            addr_w |= addr << (t * 32)
            wdata_w |= wdata << (t * 32)
            be_w |= be << (t * 4)
            tag_w |= tag << (t * 4)
            if ready & (1 << t):
                self.pending[t].add(tag)
                self.offer[t] = None
        d.core_req_valid_i.value = valid
        d.core_op_i.value = op_w
        d.core_addr_i.value = addr_w
        d.core_wdata_i.value = wdata_w
        d.core_be_i.value = be_w
        d.core_tag_i.value = tag_w

    def outstanding(self) -> int:
        return sum(len(p) for p in self.pending) + sum(
            1 for o in self.offer if o is not None
        )

    def assert_clean(self):
        assert not self.mismatches, (
            f"{len(self.mismatches)} value mismatch(es); first three:\n  "
            + "\n  ".join(self.mismatches[:3])
        )
