# NoC performance

All numbers measured by `tb/tests/test_noc_mesh.py` on the 2x2 mesh, single-flit
packets unless stated, uniform random destinations excluding self. Raw data is
written to `sim/noc_perf.json` and `sim/noc_credit_sweep.json` by the test run,
so every figure here is reproducible with `make test TEST=mesh`.

## Topology constants

| Quantity | Value | Where it comes from |
| --- | --- | --- |
| Nodes | 4 | 2x2 mesh |
| Unidirectional links | 8 | 4 edges, both directions |
| Diameter | 2 hops | corner to opposite corner |
| Mean hops, uniform-no-self | 1.333 | from tile 0: tiles 1 and 2 are 1 hop, tile 3 is 2 |
| Router pipeline | 3 stages | BW+RC, VA+SA, ST; link traversal is the wire |

## Load-latency curve

Offered load is the per-node injection probability per cycle; because packets
are single-flit, that is also flits/cycle/node. Latency is measured from packet
*generation*, so source queueing is included -- measuring from injection would
hide the queue growth that defines the knee.

| Offered | Accepted | Mean latency (cycles) |
| ---: | ---: | ---: |
| 0.02 | 0.020 | 8.1 |
| 0.05 | 0.050 | 8.1 |
| 0.10 | 0.101 | 8.4 |
| 0.15 | 0.154 | 8.7 |
| 0.20 | 0.201 | 8.8 |
| 0.25 | 0.247 | 9.2 |
| 0.30 | 0.297 | 9.4 |
| 0.40 | 0.395 | 10.1 |
| 0.50 | 0.497 | 11.9 |
| 0.70 | 0.601 | 157.0 |

```
latency
(cycles)
  160 |                                                        *
      |
  120 |
      |
   80 |
      |
   40 |
      |
   10 |  *   *   *   *   *   *   *    *     *
      +--+---+---+---+---+---+---+----+-----+-----+--> offered load
      0.02 .05 .10 .15 .20 .25 .30  .40   .50   .70
```

**Zero-load latency: 8.1 cycles.** Three router stages plus a link per hop, at
a mean of 1.333 hops, is about 5.3 cycles of pure traversal; the balance is the
injection handshake and the source queue.

**Saturation: accepted throughput flattens at about 0.60 flits/cycle/node.**
Between 0.50 and 0.70 offered, accepted rises only 0.497 -> 0.601 while mean
latency goes 11.9 -> 157.0. That is the knee, and 0.60 is the number worth
quoting.

### What the ordering rule cost

These numbers were re-measured after decision D22, which stops a packet
changing virtual channel mid-flight so that messages between one pair of tiles
cannot be reordered. A packet that finds its own channel busy now waits instead
of taking the other one, so some cost was expected. Against the same test at
the same seeds, before and after:

| Offered | Latency before | Latency after | Change |
| ---: | ---: | ---: | ---: |
| 0.10 | 8.0 | 8.4 | +5% |
| 0.25 | 8.2 | 9.2 | +12% |
| 0.40 | 8.7 | 10.1 | +16% |
| 0.50 | 10.5 | 11.9 | +13% |

**The knee did not move.** Accepted throughput at saturation is 0.601 against
0.604 before -- within noise -- because the limit there is the ejection port and
the allocator, neither of which this rule touches. What it costs is latency
under moderate load, where a packet that would have slipped onto the other
channel now queues behind its own.

That is the trade, stated as a number: **roughly 10-15% mean latency below the
knee, for point-to-point ordering.** The alternative was a protocol that loses
a transaction whenever a Put-Ack overtakes a forward, which the stress tier
measured at roughly once in ten thousand requests. It is not a close call, but
it is worth being able to say what was paid.

**Why 0.60 and not 1.0.** The ejection port is the theoretical limit: by
symmetry each node receives as much as it sends, and an eject port moves one
flit per cycle, so the ideal saturation is 1.0. Links are not the constraint --
total demand at rate r is `4 * r * 1.333` flit-hops/cycle against a link
capacity of 8, so links only bind above r = 1.5. The gap from 1.0 to 0.60 is
router efficiency, and it has two identifiable causes:

1. **Separable input-first allocation** does not find a maximum matching. Stage
   one commits to one VC per input port before stage two knows whether that
   choice conflicts; a VC that loses stage two wastes its input port's turn for
   that cycle. This is the standard cost of separable allocators and is why a
   wavefront or iterative-matching allocator buys throughput at the price of a
   longer critical path.
2. **The tail-credit VC hold (decision D9).** An output VC stays allocated from
   the moment its tail is sent until the credit for that tail returns, roughly
   a credit round trip. For single-flit packets that is most of the VC's duty
   cycle: each VC turns over about every 5 cycles rather than every cycle. With
   six VCs per output port that is still ~1.2 packets/cycle of aggregate
   capacity, above the 1.0 the eject port can absorb -- but the three vnets are
   chosen at random by the traffic generator, so the per-vnet imbalance bites
   before the aggregate does.

## Traffic patterns

60 packets each, mixed 1- and 3-flit, every flit delivered exactly once at the
addressed tile with no reordering.

| Pattern | Flits | Mean packet latency |
| --- | ---: | ---: |
| uniform | 86 | 23.7 |
| tornado | 98 | 21.8 |
| hot-spot (all to tile 3) | 102 | 57.6 |

Tornado is not worse than uniform here, which is a 2x2 artifact rather than a
general result: `(x + X/2) mod X` with X = 2 is just "swap with your horizontal
neighbour", a 1-hop permutation that uses only the two horizontal link pairs. On
a larger mesh tornado is the pattern that maximises distance and it would be
clearly worse. Worth saying out loud rather than presenting 21.8 as evidence the
network handles adversarial permutations well.

Hot-spot is 2.4x uniform, and that is the honest and expected result: four
sources share one ejection port, so the offered load at tile 3 is four times
what any single port can absorb and the excess queues. Tile 3 is where the
memory controller hangs in the full system, so this is the traffic the
coherence phases will actually generate when several directories miss to memory
at once -- not a synthetic worst case.

## Credit round trip and buffer depth

Traced through this implementation, from a flit launching to the credit for it
being usable again:

| Cycle | Event |
| --- | --- |
| T | upstream switch allocation grants; credit counter decrements at the edge |
| T+1 | flit is on the link (output register) |
| T+2 | flit has been written into the downstream VC buffer; readable |
| T+3 | downstream pops it; credit return register is set |
| T+4 | upstream credit counter increments; the credit is usable |

**Round trip = 4 cycles.** `VC_DEPTH = 4`, so a single VC can keep exactly four
flits in flight and covers the round trip with nothing left over: at full rate
the upstream launches on cycles T..T+3, and the first credit returns at T+4
precisely when it is needed. There is no throughput left on the table for a
single VC, and no margin either -- one more cycle of credit latency, from a
pipelined link or a registered credit decode, would open a one-cycle bubble
every four flits and `VC_DEPTH` would have to go to 5.

Note this is about a *single VC at full rate*. Decision D8 records that the FIFO
refuses a write while full even with a concurrent read, so under sustained
full-duplex traffic the buffer settles at an occupancy of 3, not 4. That does
not reduce the credit-limited rate, because at steady state the buffer is not
full.

### The rule is about throughput, not correctness

This is the common trap, so it is measured rather than asserted. Stretching the
downstream credit return delay lengthens the round trip without changing
anything else:

| Credit delay | Packets delivered | Accepted (flits/cyc/node) | Mean latency |
| ---: | ---: | ---: | ---: |
| 1 | 1859 / 1859 | 0.308 | 8.4 |
| 4 | 1833 / 1833 | 0.303 | 9.1 |
| 8 | 1833 / 1833 | 0.299 | 14.4 |
| 16 | 1818 / 1818 | 0.222 | 280.1 |

Every packet is delivered at every delay, including at 16 cycles -- four times
`VC_DEPTH`. Nothing is lost, nothing is corrupted, and the router's credit
assertions (no send on zero credits, no buffer overflow, counter bounds) stay
quiet throughout. What degrades is throughput and latency: at delay 16 a VC
spends most of its life waiting for credits it has already earned, and latency
rises 33x.

**One buffer per VC is sufficient for correctness.** Too few buffers idles the
link while credits return; it does not break the protocol. A design that loses
flits when buffers are too shallow has a credit-accounting bug, not a sizing
problem.

## Effect of VC count

Not measured. `VCS_PER_VNET` is a package localparam rather than a module
parameter, so sweeping it would mean rebuilding the package per data point, and
the harness currently assumes two VCs per vnet when it picks an injection VC.
The credit-delay sweep above probes the same underlying mechanism -- how long a
VC is unavailable after use -- without that rebuild, which is why it is the
experiment that was run.

What can be said without measuring: the two VCs per vnet here are for
head-of-line blocking relief only, not for deadlock freedom. Deadlock freedom
comes from XY routing for the network and from the three virtual networks for
the protocol; a single VC per vnet would still be deadlock-free, and would just
block more. That distinction is a standard follow-up question and is argued in
`docs/deadlock.md`.
