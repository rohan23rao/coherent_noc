# Deadlock freedom

Two separate arguments are needed, and conflating them is the most common
mistake in this area. **Routing deadlock** is about cycles in the channel
dependency graph and is solved by the routing function. **Protocol deadlock** is
about cycles in the message dependency graph and is solved by virtual networks.
Neither one implies the other: a perfectly deadlock-free routing function on a
network with one virtual network will still deadlock the moment a request cannot
be delivered because the buffer ahead of it is full of responses that cannot be
generated until that request is served.

This file covers the routing argument, which Phase 3 completes. The protocol
argument is filled in as the coherence phases land.

---

## 1. Routing deadlock: why XY is safe

**Claim.** Dimension-ordered XY routing on a mesh cannot deadlock, for any
buffer depth, with a single virtual channel.

**Argument.** Deadlock requires a cycle in the channel dependency graph: a set
of channels `c0 -> c1 -> ... -> cn -> c0` where each holds a flit waiting on the
next. Under XY, a packet consumes all of its X displacement before any of its Y
displacement. So a packet may turn from an X channel to a Y channel, but never
from a Y channel back to an X channel.

In the standard turn model there are eight turns in a 2-D mesh. XY forbids four
of them -- every turn that leaves a Y channel for an X channel:

| Turn | Allowed under XY? |
| --- | --- |
| East -> North | yes |
| East -> South | yes |
| West -> North | yes |
| West -> South | yes |
| North -> East | **no** |
| North -> West | **no** |
| South -> East | **no** |
| South -> West | **no** |

With those four removed, the dependency graph is layered: X channels may depend
on Y channels, and Y channels may depend only on other Y channels and on the
ejection port. A cycle would require a Y channel to depend on an X channel,
which requires a forbidden turn. Therefore no cycle exists, and therefore no
routing deadlock.

Two properties fall out of the same argument and are worth stating because they
are what make the proof usable:

* **It holds for any buffer depth, including one flit.** Buffering affects
  throughput, never routing-deadlock freedom. See `docs/noc_perf.md` for the
  measured version of that claim.
* **It needs no virtual channels.** The two VCs per vnet in this design are for
  head-of-line blocking relief only. Deleting one of them would cost throughput
  and nothing else.

**What the assertions check.** The proof above is about the routing function, so
the assertions check that the implementation matches the function:

| Property | Assertion | Where |
| --- | --- | --- |
| No flit leaves the mesh | `a_no_east_escape`, `a_no_west_escape`, `a_no_north_escape`, `a_no_south_escape` | `noc_top.sv` |
| An ejected flit is addressed here | `a_eject_is_mine` | `noc_top.sv` |
| A packet never changes vnet | `a_no_vnet_change`, `a_same_vnet` | `input_unit.sv`, `vc_allocator.sv` |
| One input per output port | `a_one_input_per_output` | `switch_allocator.sv` |
| A VC is never allocated while busy | `a_no_busy_realloc` | `vc_allocator.sv` |
| A head only enters an idle VC | `a_head_only_when_idle` | `input_unit.sv` |
| No flit sent without credit | `a_no_send_without_credit` | `credit_counter.sv` |
| No VC buffer overflow | `a_no_overflow` | `input_unit.sv` |

The last two are deliberately redundant: the credit counter proves no flit is
sent without a credit, and the buffer overflow check proves independently that
no flit arrives at a full buffer. If credit accounting is correct these can
never both matter, which is exactly why both are present -- a single check would
be testing the accounting against itself.

### What XY costs, and the alternatives

XY has **zero path diversity**: between any two nodes there is exactly one
route. A hot link cannot be routed around, and a faulty link disconnects the
pair outright. The hot-spot measurement in `docs/noc_perf.md` (2.4x uniform
latency, all traffic to tile 3) is the visible consequence.

| Alternative | What it buys | What it costs |
| --- | --- | --- |
| **O1TURN** | Two routes per pair (XY or YX, chosen per packet), roughly doubling path diversity, and provably near-optimal worst-case throughput on a mesh | Two virtual networks *for routing*, on top of the three the protocol needs, because XY and YX traffic must not share channels or the combined turn set has cycles again |
| **West-first / north-last / negative-first** | Partial adaptivity with no extra VCs -- the turn model forbids just enough turns to stay acyclic | Adaptivity only in some directions; the benefit is asymmetric and traffic-dependent |
| **Fully adaptive with escape VC** | Maximum path diversity; routes around congestion in any direction | One VC per vnet reserved as a deadlock-free escape path that is itself XY-routed, plus the logic to decide when to drop into it. The escape VC is doing the real correctness work, and the adaptive channels are an optimization on top |

**Why XY here.** With four nodes and a diameter of two, path diversity has
almost nothing to offer: the only two-hop pair is the diagonal, and its two
candidate routes cross the same two link pairs. The cost of adaptivity -- extra
VCs, a more complex allocator, and a harder deadlock argument -- would be paid
in full against a benefit that a 2x2 cannot express. At 8x8 the calculation
inverts, and the first thing to reach for would be O1TURN, because it doubles
path diversity for one extra VC set and keeps the per-route argument as simple
as the one above.

---

## 2. Protocol deadlock: the message dependency graph

*Filled in as the coherence phases land. The design intent, from the
specification: a request may cause a forward, a forward may cause a response,
and a response causes nothing. Three levels of dependency means three virtual
networks, and the argument only holds if the last level is a true sink -- a VN2
message must always be accepted by its destination in bounded time without the
destination needing to send anything first.*
