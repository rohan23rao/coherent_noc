"""Driver/monitor harness for a standalone router.

The harness plays both neighbours of the DUT at once:

  * **Upstream** -- it owns credits for each of the DUT's input VCs, starting at
    VC_DEPTH, and never launches a flit without one. It also mirrors the
    router's own VC discipline: a new packet may not start on an input VC until
    the DUT has returned the *tail* credit for that VC, which is exactly when
    that VC goes back to IDLE. Violating this would trip the DUT's
    a_head_only_when_idle assertion, so the harness has to be as well-behaved as
    a real neighbour.

  * **Downstream** -- it accepts every flit the DUT emits and returns a credit
    `credit_delay` cycles later, one per output port per cycle. Stretching that
    delay is how the credit-starvation tests apply backpressure without
    touching the RTL.
"""

from collections import deque

from tbutil import step, u

from .flit import FLIT_W, Flit, unpack_port

NUM_PORTS = 5
VCS_PER_VNET = 2
NUM_VNETS = 3
VCS_PER_PORT = NUM_VNETS * VCS_PER_VNET
VC_SEL_W = 3
VC_DEPTH = 4

PORT_NORTH, PORT_EAST, PORT_SOUTH, PORT_WEST, PORT_LOCAL = range(5)
PORT_NAMES = ["N", "E", "S", "W", "L"]


def vc_index(vnet: int, vc_id: int) -> int:
    return (vnet << 1) | vc_id


class RouterHarness:
    def __init__(self, dut, credit_delay: int = 1):
        self.dut = dut
        self.credit_delay = credit_delay
        self.cycle = 0

        # Upstream state: credits we hold for each DUT input VC, and which of
        # those VCs currently carries an unfinished packet.
        self.credits = [[VC_DEPTH] * VCS_PER_PORT for _ in range(NUM_PORTS)]
        self.vc_busy = [[False] * VCS_PER_PORT for _ in range(NUM_PORTS)]

        # Packets waiting to be injected, per input port.
        self.pending = [deque() for _ in range(NUM_PORTS)]
        # The packet currently streaming out of each input port, if any.
        self.in_flight = [None] * NUM_PORTS

        # Downstream state.
        self.received = []                       # (out_port, Flit, cycle)
        self.credit_q = [deque() for _ in range(NUM_PORTS)]

        self.sent = []                           # (in_port, Flit, cycle)

    # -- stimulus ----------------------------------------------------------
    def queue_packet(self, in_port: int, flits: list, vnet: int):
        """Queue a whole packet for injection on `in_port`."""
        assert flits and flits[0].head and flits[-1].tail
        self.pending[in_port].append((vnet, flits))

    def all_sent(self) -> bool:
        return all(not q for q in self.pending) and all(f is None for f in self.in_flight)

    # -- internals ---------------------------------------------------------
    def _free_vc(self, port: int, vnet: int):
        for vc_id in range(VCS_PER_VNET):
            idx = vc_index(vnet, vc_id)
            if not self.vc_busy[port][idx]:
                return idx
        return None

    def _sample_outputs(self):
        dut = self.dut
        valid = u(dut.flit_valid_o)
        word = u(dut.flit_o)
        for p in range(NUM_PORTS):
            if valid & (1 << p):
                flit = unpack_port(word, p)
                self.received.append((p, flit, self.cycle))
                # Return the credit for the VC this flit landed in downstream.
                self.credit_q[p].append(
                    (self.cycle + self.credit_delay,
                     vc_index(flit.vnet, flit.vc_id),
                     flit.tail)
                )

    def _sample_credits_back(self):
        """Credits the DUT returns to us for its input VCs."""
        dut = self.dut
        valid = u(dut.credit_valid_o)
        vc_word = u(dut.credit_vc_o)
        tail_word = u(dut.credit_tail_o)
        for p in range(NUM_PORTS):
            if valid & (1 << p):
                vc = (vc_word >> (p * VC_SEL_W)) & ((1 << VC_SEL_W) - 1)
                self.credits[p][vc] += 1
                assert self.credits[p][vc] <= VC_DEPTH, (
                    f"port {p} VC {vc}: credit overflow, DUT returned more "
                    f"credits than flits were sent"
                )
                if tail_word & (1 << p):
                    # The DUT read the tail out, so that input VC is idle again.
                    self.vc_busy[p][vc] = False

    def _drive_credits_downstream(self):
        dut = self.dut
        valid = 0
        vc_word = 0
        tail_word = 0
        for p in range(NUM_PORTS):
            q = self.credit_q[p]
            if q and q[0][0] <= self.cycle:
                _, vc, is_tail = q.popleft()
                valid |= 1 << p
                vc_word |= vc << (p * VC_SEL_W)
                if is_tail:
                    tail_word |= 1 << p
        dut.credit_valid_i.value = valid
        dut.credit_vc_i.value = vc_word
        dut.credit_tail_i.value = tail_word

    def _drive_inputs(self):
        dut = self.dut
        valid = 0
        flit_word = 0

        for p in range(NUM_PORTS):
            # Start a new packet if the port is idle and a VC is free.
            if self.in_flight[p] is None and self.pending[p]:
                vnet = self.pending[p][0][0]
                vc = self._free_vc(p, vnet)
                if vc is not None:
                    _, flits = self.pending[p].popleft()
                    self.in_flight[p] = {"vc": vc, "flits": deque(flits)}
                    self.vc_busy[p][vc] = True

            cur = self.in_flight[p]
            if cur is None:
                continue
            vc = cur["vc"]
            if self.credits[p][vc] == 0:
                continue

            flit = cur["flits"][0]
            flit.vc_id = vc & 1
            flit.vnet = vc >> 1
            valid |= 1 << p
            flit_word |= flit.pack() << (p * FLIT_W)
            self.credits[p][vc] -= 1
            cur["flits"].popleft()
            self.sent.append((p, flit, self.cycle))
            if not cur["flits"]:
                self.in_flight[p] = None

        dut.flit_valid_i.value = valid
        dut.flit_i.value = flit_word

    async def tick(self):
        """Advance one cycle: sample what the DUT did, then drive the next."""
        await step(self.dut)
        self.cycle += 1
        self._sample_outputs()
        self._sample_credits_back()
        self._drive_inputs()
        self._drive_credits_downstream()

    async def run(self, cycles: int):
        for _ in range(cycles):
            await tick_guard(self)

    async def drain(self, max_cycles: int = 2000) -> bool:
        """Run until everything queued has been injected and ejected."""
        expected = None
        for _ in range(max_cycles):
            await self.tick()
            if self.all_sent():
                if expected is None:
                    expected = len(self.sent)
                if len(self.received) >= expected:
                    # Let the pipeline settle a few cycles past the last eject.
                    for _ in range(4):
                        await self.tick()
                    return True
        return False

    def idle(self):
        self.dut.flit_valid_i.value = 0
        self.dut.flit_i.value = 0
        self.dut.credit_valid_i.value = 0
        self.dut.credit_vc_i.value = 0
        self.dut.credit_tail_i.value = 0


async def tick_guard(h: RouterHarness):
    await h.tick()


# ---------------------------------------------------------------------------
# Mesh-level harness (Phase 3)
# ---------------------------------------------------------------------------

NUM_TILES = 4
MESH_X = 2


def tile_xy(tile: int):
    return tile % MESH_X, tile // MESH_X


def hops(src: int, dst: int) -> int:
    sx, sy = tile_xy(src)
    dx, dy = tile_xy(dst)
    return abs(dx - sx) + abs(dy - sy)


def dest_uniform(rng, src: int) -> int:
    """Any tile including the source's own."""
    return rng.randrange(NUM_TILES)


def dest_uniform_no_self(rng, src: int) -> int:
    dst = rng.randrange(NUM_TILES - 1)
    return dst if dst < src else dst + 1


def dest_tornado(rng, src: int) -> int:
    """(x + X/2) mod X, same y. In a 2x2 this is the horizontal neighbour."""
    x, y = tile_xy(src)
    return ((x + MESH_X // 2) % MESH_X) + y * MESH_X


def dest_hotspot(rng, src: int) -> int:
    """Everyone to tile 3, which is where the memory controller lives."""
    return 3


PATTERNS = {
    "uniform": dest_uniform_no_self,
    "tornado": dest_tornado,
    "hotspot": dest_hotspot,
}


class MeshHarness:
    """Traffic generator and monitor for all four local ports of noc_top.

    Latency is measured from the cycle a packet is *generated* to the cycle its
    tail is ejected, so source-queueing delay is included. Measuring from
    injection instead would hide exactly the queue growth that defines the knee
    of the load-latency curve.
    """

    def __init__(self, dut, credit_delay: int = 1):
        self.dut = dut
        self.credit_delay = credit_delay
        self.cycle = 0

        self.credits = [[VC_DEPTH] * VCS_PER_PORT for _ in range(NUM_TILES)]
        self.vc_busy = [[False] * VCS_PER_PORT for _ in range(NUM_TILES)]

        self.src_q = [deque() for _ in range(NUM_TILES)]   # generated, not yet injected
        self.in_flight = [None] * NUM_TILES
        self.credit_q = [deque() for _ in range(NUM_TILES)]

        self.generated = 0
        self.injected_flits = 0
        self.ejected = []          # (dst_tile, Flit, cycle)
        self.pkt_meta = {}         # payload of head -> (src, dst, gen_cycle, n_flits)
        self.latencies = []        # completed packet latencies
        self.next_id = 1

    # -- generation --------------------------------------------------------
    def generate(self, src: int, dst: int, n_flits: int, vnet: int):
        pid = self.next_id
        self.next_id += 1
        base = pid << 12
        dx, dy = tile_xy(dst)
        flits = [
            Flit(
                head=1 if i == 0 else 0,
                tail=1 if i == n_flits - 1 else 0,
                dst_x=dx,
                dst_y=dy,
                src_id=src,
                payload=base + i,
            )
            for i in range(n_flits)
        ]
        self.src_q[src].append((vnet, flits))
        self.pkt_meta[base] = (src, dst, self.cycle, n_flits)
        self.generated += 1
        return base

    # -- internals ---------------------------------------------------------
    def _free_vc(self, tile: int, vnet: int):
        for vc_id in range(VCS_PER_VNET):
            idx = vc_index(vnet, vc_id)
            if not self.vc_busy[tile][idx]:
                return idx
        return None

    def _sample(self):
        dut = self.dut
        valid = u(dut.eject_valid_o)
        word = u(dut.eject_flit_o)
        for t in range(NUM_TILES):
            if valid & (1 << t):
                flit = unpack_port(word, t)
                self.ejected.append((t, flit, self.cycle))
                self.credit_q[t].append(
                    (self.cycle + self.credit_delay,
                     vc_index(flit.vnet, flit.vc_id),
                     flit.tail)
                )
                if flit.tail:
                    # Payloads are base + flit_index with base = packet_id << 12,
                    # so masking off the low 12 bits recovers the packet.
                    head_base = (flit.payload >> 12) << 12
                    meta = self.pkt_meta.get(head_base)
                    if meta is not None:
                        _, _, gen_cycle, _ = meta
                        self.latencies.append(self.cycle - gen_cycle)

        cvalid = u(dut.inject_credit_valid_o)
        cvc = u(dut.inject_credit_vc_o)
        ctail = u(dut.inject_credit_tail_o)
        for t in range(NUM_TILES):
            if cvalid & (1 << t):
                vc = (cvc >> (t * VC_SEL_W)) & ((1 << VC_SEL_W) - 1)
                self.credits[t][vc] += 1
                assert self.credits[t][vc] <= VC_DEPTH, (
                    f"tile {t} VC {vc}: credit overflow"
                )
                if ctail & (1 << t):
                    self.vc_busy[t][vc] = False

    def _drive(self):
        dut = self.dut
        valid = 0
        flit_word = 0
        for t in range(NUM_TILES):
            if self.in_flight[t] is None and self.src_q[t]:
                vnet = self.src_q[t][0][0]
                vc = self._free_vc(t, vnet)
                if vc is not None:
                    _, flits = self.src_q[t].popleft()
                    self.in_flight[t] = {"vc": vc, "flits": deque(flits)}
                    self.vc_busy[t][vc] = True

            cur = self.in_flight[t]
            if cur is None:
                continue
            vc = cur["vc"]
            if self.credits[t][vc] == 0:
                continue
            flit = cur["flits"][0]
            flit.vc_id = vc & 1
            flit.vnet = vc >> 1
            valid |= 1 << t
            flit_word |= flit.pack() << (t * FLIT_W)
            self.credits[t][vc] -= 1
            cur["flits"].popleft()
            self.injected_flits += 1
            if not cur["flits"]:
                self.in_flight[t] = None

        dut.inject_valid_i.value = valid
        dut.inject_flit_i.value = flit_word

        cvalid = 0
        cvc = 0
        ctail = 0
        for t in range(NUM_TILES):
            q = self.credit_q[t]
            if q and q[0][0] <= self.cycle:
                _, vc, is_tail = q.popleft()
                cvalid |= 1 << t
                cvc |= vc << (t * VC_SEL_W)
                if is_tail:
                    ctail |= 1 << t
        dut.eject_credit_valid_i.value = cvalid
        dut.eject_credit_vc_i.value = cvc
        dut.eject_credit_tail_i.value = ctail

    async def tick(self):
        await step(self.dut)
        self.cycle += 1
        self._sample()
        self._drive()

    def idle(self):
        d = self.dut
        d.inject_valid_i.value = 0
        d.inject_flit_i.value = 0
        d.eject_credit_valid_i.value = 0
        d.eject_credit_vc_i.value = 0
        d.eject_credit_tail_i.value = 0

    def backlog(self) -> int:
        return sum(len(q) for q in self.src_q) + sum(
            1 for f in self.in_flight if f is not None
        )
