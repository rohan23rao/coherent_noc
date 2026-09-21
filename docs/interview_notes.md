# Interview notes

A spoken overview, then the ten questions worth being asked about this design
and the answers I would give. These are the answers I can defend, which is not
always the same as the answers that sound best.

---

## The 90-second overview

Four tiles on a 2x2 mesh. Each tile has a private two-way L1, a slice of a
shared inclusive L2 that doubles as the directory for the quarter of the
address space it homes, its own memory controller, and a network interface.
Home bank is picked by address bits 6 and 5, so it sits below the index field
and has to be carried in the L1 tag -- if it is not, two lines that differ only
in home bank alias onto each other.

The protocol is directory MESI out of the Sorin, Hill and Wood primer: four
stable states and nine transients at the cache, five at the directory. The
transients are the design. `IM_AD` versus `IM_A` is "waiting for data" versus
"waiting for acks", and the count of outstanding acks is a **signed** field,
because an invalidation acknowledgement can arrive before the data message
that says how many to expect.

Three virtual networks, assigned by dependency depth rather than by who sends
what: requests can block, forwards can block only on transient state, and
responses are a true sink. That is the deadlock argument, and it is physically
implemented three times over -- separate queues in the network interface,
separate ejection paths, separate credits -- because the first two times it was
only implemented twice and the machine deadlocked over the network while every
direct-connect test passed.

The router is a conventional four-stage wormhole router with virtual channels,
XY dimension-ordered routing, credit flow control and separable input-first
allocation. Packets keep the virtual channel they were injected on, which makes
each channel an independent FIFO subnetwork -- the coherence protocol needs
point-to-point ordering and this is where it comes from.

Verification is six tiers -- unit, network, tables, directed, constrained
random and mutation -- and a coverage report that lists every legal table cell
the runs did not reach along with the argument for why it is unreachable. The
bug log has twenty-three entries; the interesting ones are all in the last two
tiers.

---

## 1. Why three virtual networks, and not two or four?

Three, because the message dependency chain is three deep and no deeper.

A request can generate a forward. A forward can generate a response. A response
generates nothing -- it is consumed by an MSHR that was allocated before the
request went out, which is why it can always be accepted. Give each level of
that chain its own buffering and the dependency graph is a chain rather than a
cycle, which is the whole argument.

Two does not work: put forwards and responses together, and a forward that the
receiving cache must stall -- `IM_AD` + Fwd-GetM, say -- sits at the head of a
queue in front of the response that would let the cache accept it. That is a
deadlock, and it is not exotic; race R6 produces it in about three hundred
cycles.

Four is possible but buys nothing here. You would add one if you had a message
class that can depend on a response -- writeback acknowledgements in some
protocols, or a coherent DMA engine whose completions generate further
requests. Ours does not: `WB-Data` is a response and its Put-Ack is a forward,
both already covered.

The honest caveat: three virtual networks make the protocol deadlock-free, not
the network. Routing deadlock is a separate argument, and XY dimension-ordered
routing is what handles it.

## 2. Why must `ack_cnt` be signed?

Because invalidation acknowledgements and the data that tells you how many to
expect travel on different paths and arrive in any order.

A core in `IM_AD` has asked for the line for writing. The directory sends it
Data with an AckCount of, say, two, and sends invalidations to the two sharers.
Those sharers acknowledge **to the requester**, not to the directory -- that is
what makes the protocol scale, since the directory does not serialise the
acknowledgements. So the requester can receive both Inv-Acks before the Data.
Its count goes to minus two, and then the Data credits it back up to zero.

An unsigned counter wraps on the first early ack. With a four-bit field, minus
one becomes fifteen, and the transaction never completes -- it does not fail
loudly, it simply waits forever. That is race R1, and the mutation test for it
does not delete an arc at all: it changes the completion comparison, and the
test still fails.

The related trap: completion is "the count reached zero **after** the data
arrived", never just "the count is zero". It is zero before the transaction
starts.

## 3. What breaks if `S_D` accepts a GetS?

The reader gets a value that was overwritten.

`S_D` means the directory has forwarded a GetS to the owner and has not yet
received the owner's data. Its own L2 copy is stale **by construction**: the
owner had the line in M and wrote to it without telling the directory. That is
the entire point of M. So answering a third core's GetS out of the L2 in that
window returns a value some other core overwrote, with nothing anywhere to
notice -- no assertion fires, no message is lost, the protocol stays internally
consistent and the data is wrong.

That is race R7, and it is the one I would use to argue that a value check is
not optional. Every structural assertion in the design passes on the mutated
version.

The asymmetry worth mentioning: `S_D` stalls GetS and GetM but **accepts every
Put**. Stalling Puts too would deadlock, because the cache that owes this
directory its data may itself be waiting on a Put-Ack from here.

## 4. Why must E send data to the directory on a Fwd-GetS?

Because the directory cannot tell whether the core silently upgraded.

E exists so that a read followed by a write costs one transaction instead of
two: a core in E may go to M without sending anything. The price is that the
directory's record says E while the line may be dirty. So when it forwards a
GetS to that core, the core has to send two messages -- the data to the
requester, and a refreshed copy to the directory -- because neither the
directory nor the core can prove the line is still clean.

The same reasoning gives the other half: a directory in E receiving a PutM from
the owner must **take the data**, even though it thought the line was clean.
That is race R11, and dropping it loses the store made after the silent
upgrade.

And the case that follows from it, which is bug B21: a PutE is a promise that
the core never wrote, so the directory's copy is current again and its
"my copy is current" flag has to be set again. Clearing that flag on granting E
and never restoring it is invisible for the whole life of the line -- until a
back-invalidation consults it, believes the copy is stale, frees the way
without writing back, and loses every store that had been written into it.

## 5. What happens when a back-invalidation hits a line held in M?

Capacity pressure in the *shared* cache destroying a *private* cache's dirty
line -- which is the thing to be able to explain cold about an inclusive L2.

The L2 needs a way. It is strictly inclusive, so it cannot simply drop the
line: every L1 copy has to go first. It allocates a transaction buffer entry,
sends a recall, waits for the responses, writes any dirty data back to memory,
and only then frees the way and replays the request that triggered it.

The recall reuses the `Fwd-GetM` arc at the cache rather than adding a
data-carrying invalidation. From the cache's point of view a recall *is* a
Fwd-GetM: give up the line, hand over the data, go to I. Every transient case
the table already handles -- a recall arriving while the line is in `MI_A`, or
in `SM_AD` waiting for acks -- comes out right for free, and for the same
reason. What differs is only the reply's address: WB-Data to the home bank
rather than owner-data to a requesting cache.

The cost of inclusion is exactly this: the L2 dictates what the L1s may hold,
so an L2 conflict miss evicts from caches that had no conflict. An exclusive or
non-inclusive LLC removes that, and buys a different problem -- you need a
separate directory structure, because the LLC tags no longer tell you who has
what, and that structure has its own capacity and its own back-invalidations
when *it* overflows.

## 6. Why is the credit depth rule about throughput and not correctness?

Because correctness only needs one credit; depth buys you the ability to keep
sending while the first credit is on its way back.

A sender may transmit a flit when it holds a credit for the destination virtual
channel. With a single buffer, it sends one flit and then waits for the credit
round trip -- the flit's arrival, the buffer's read, the credit's return -- and
the channel idles for that whole period. The rule of thumb is to size the buffer
at the credit round-trip latency so a channel can stay saturated; here that is
three cycles of round trip against a depth of four.

Getting it wrong slows the network down. It does not break it, and it cannot:
the credit counter never lets a sender exceed the buffer, so an undersized
buffer is a performance bug and an oversized one is wasted area.

The depth-four number here has a second, sharper justification: a virtual
channel holds one packet at a time, and the longest packet is a head plus two
body flits. Four is three plus one -- the whole longest packet, plus one slot
so the credit round trip does not stall a channel that is otherwise full. That
is also why VC occupancy four is unreachable, which the coverage report says
out loud rather than leaving as a hole.

## 7. What does XY cost, and when would you pay for adaptive routing?

XY costs you the ability to route around congestion, and it buys you a
deadlock-freedom argument that fits in a sentence: a packet only ever turns
from X to Y, never from Y to X, so the channel dependency graph is acyclic.
No virtual channels are needed for routing deadlock at all -- the ones here are
for the protocol and for throughput.

What it costs shows up under non-uniform traffic. On a hot spot -- every tile
hammering one bank -- XY funnels everything down the same dimension order and
the knee in the load-latency curve arrives earlier than it needs to.
`docs/noc_perf.md` has the number.

I would pay for adaptive routing when the traffic is known to be non-uniform
and the network is big enough for alternate paths to exist. On a 2x2 mesh there
is essentially one path between any pair, so adaptive routing has nothing to
adapt to -- it is strictly worse here, more logic for no alternative. At 8x8
with a hot spot it is a different conversation, and the standard answer is
Duato's protocol: an adaptive virtual channel plus an escape channel that is
dimension-ordered, so the acyclic argument still exists for packets that need
it.

## 8. How does the directory serialise two simultaneous GetMs, and what does
that imply for fairness?

It does not serialise them at all in any global sense -- it serialises them at
the **bank**, and that is the whole ordering point of a directory protocol.
Each line has exactly one home bank, the bank handles one message at a time,
and the order it happens to pop them off its request queue *is* the coherence
order for that line.

Concretely: two GetMs arrive. The first finds the directory in M with some
owner, sends that owner a Fwd-GetM, and **sets the owner to the requester at
the moment it forwards, not when the data lands**. So the second GetM finds the
directory already naming the first requester as owner and forwards to it. The
line moves in a chain, cache to cache, and the directory is out of the way after
the first cycle.

Two consequences worth stating. The second forward can reach a cache that is
still in `IM_AD` -- it does not have the data yet -- so the table has to stall
there, and that stall is safe only because the data is on a virtual network
that cannot itself be blocked. That is race R6.

And the fairness: there is none, beyond whatever the network and the bank's
input queue give you. A core whose requests consistently arrive later loses
consistently. On four tiles that is not visible; at scale you would want a
mechanism -- a fairness counter at the bank, or a token, or at minimum a
measurement showing the distribution. The design does not have one, and I would
not claim it does.

## 9. How does this scale to 64 tiles, and what breaks first?

The **full-vector sharer list** breaks first, and it breaks on area. One bit per
tile per line, stored beside every L2 tag: at 64 tiles that is 64 bits of
directory state for a 256-bit line, a quarter of the array spent on metadata.
The standard answers are a limited-pointer scheme -- store four sharer IDs and
fall back to broadcast beyond that -- or a coarse vector where one bit covers a
group of tiles, both of which trade precision for area and generate spurious
invalidations.

Second is **XY routing under non-uniform traffic**, as above.

Third, and least obvious, is the **ordering property this design now relies
on**. A packet keeps the virtual channel it was injected on, which is how
point-to-point ordering is obtained; with a fixed number of channels per
virtual network, more tiles means more source tiles sharing a channel and more
head-of-line blocking between unrelated flows. At 64 tiles I would want to
revisit that trade -- either more channels, or a protocol change that tolerates
reordering by acknowledging forwards explicitly.

The 2-bit owner field and the four-entry MSHR and TBE files are limits too, but
they are parameters, not designs; they widen.

What does *not* break: the protocol itself. The state tables are independent of
tile count, and the deadlock argument is about dependency depth, which does not
grow.

## 10. How would you verify the memory consistency model, which this testbench
does not?

First, by being clear that it does not, and why the distinction matters.
Coherence is a per-line property with a simple sequential specification --
there exists a total order per line consistent with each core's program order
-- and that is checkable by comparing every retired operation against an atomic
memory, which is what `tb/models/golden.py` does. Consistency is a property of
orderings *across* lines, and an atomic model with one operation in flight per
core cannot distinguish SC from TSO from anything weaker.

To verify it, three things have to change.

**The driver** has to issue multiple operations per core without waiting, and
record program order per core. Right now it issues one at a time per line,
which is what makes the golden model's answer well defined -- and which is also
why bug B16 survived ten thousand random requests, since a forward could never
meet a miss on the same line.

**The checker** stops being a value comparison and becomes a graph. The
standard approach is axiomatic: build a relation over the executed operations
-- program order per core, reads-from edges, and the coherence order the design
actually produced -- and test it for a cycle under the candidate model's
axioms. That is what `herd` and `litmus` do for real ISAs, and what a
TSO-checker like `dat3m` does mechanically.

**The stimulus** stops being uniform random and becomes litmus tests: `SB`,
`MP`, `LB`, `IRIW` and the rest, each a handful of operations whose permitted
outcomes under each model are known, run many times with randomised delays.
Random traffic almost never produces the interleavings these tests are about;
you have to ask for them, which is the same lesson the race catalogue teaches.

The honest final note: this design has no store buffer, no fences and no
speculative execution, so there is very little consistency model to verify --
the interesting weakening all lives in the core, which is not part of this
project.
