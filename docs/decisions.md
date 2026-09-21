# Design decisions

Each entry is an argument, not a description: decision → alternatives → why →
what it costs. Entries are added as the decision is made, not at the end.

---

## D1. The L1 tag carries the home-bank bits (deviation from SPEC.md)

**Decision.** L1 index is `addr[12:7]`; the L1 tag is the *non-contiguous*
concatenation `{addr[31:13], addr[6:5]}`, 21 bits.

**Why this is a deviation.** SPEC.md's address map gives `[4:0]` offset, `[6:5]`
home bank, `[12:7]` L1 index, `[31:7]` L1 tag. Taken literally, an L1 line's
identity is `{tag[31:7], index[12:7]}`, which reduces to `addr[31:7]` — and
`addr[6:5]` is then stored nowhere in the L1. Two distinct 32-byte lines whose
addresses differ only in the home-bank field would occupy the same set, match on
the same tag, and alias onto one another. That is silent data corruption, and it
falls out of exactly the interleaving the spec chose bank-bits-low to create: the
four lines at `0x000, 0x020, 0x040, 0x060` are consecutive lines on four
different home banks, and they are precisely the addresses that collide.

**Alternatives considered.**

- *Index = `addr[10:5]`, tag = `addr[31:11]` (contiguous, 21 bits).* Simpler
  comparator, no non-contiguity. Rejected: index+offset then spans 11 bits, under
  a 4 KB page offset, which makes the L1 legally VIPT-able and directly
  contradicts SPEC.md's PIPT argument and its "index plus offset is 13 bits"
  claim. It would also un-interleave the sets with respect to banks, so a
  single L1 set would only ever hold lines from one home bank — a 4x reduction in
  effective associativity for bank-strided access patterns.
- *Store `addr[31:7]` as written and accept the aliasing.* Rejected: it is a
  correctness bug, not a tradeoff.

**Cost.** The tag comparator and the tag-construction path are non-contiguous, so
`addr_l1_tag()` is a two-slice concatenation rather than a single range. In RTL
this is free — it is wiring, not logic. The real cost is that a reader must be
told, which is why it is in the `coh_pkg.sv` header comment and here.

---

## D2. `msg_type` is 5 bits, not 4 (deviation from SPEC.md)

**Decision.** The head-flit `msg_type` field is 5 bits.

**Why.** The full message set is 18 types: 5 L1→directory requests (GetS, GetM,
PutS, PutM, PutE), 2 directory→memory requests (MemRead, MemWrite), 4 forwards
(Fwd-GetS, Fwd-GetM, Inv, Put-Ack), 1 recall, and 6 responses (Data, DataE,
Data-from-owner, Inv-Ack, WB-Data, MemData). SPEC.md's 4-bit field holds 16. Even
before the Phase 9 recall, the set is at 17 with zero headroom, and a field that
is exactly full is a field that forces message-type overloading the first time
the protocol grows.

**Alternatives considered.**

- *Keep 4 bits and overload.* Collapse MemData onto Data, and encode the recall
  as Fwd-GetM plus a flag bit elsewhere. Rejected: it makes the directory's
  message decode depend on two fields instead of one, and it destroys the ability
  to write the assertion "a recall is never sent to a non-owner" as a simple
  decode on `msg_type`.
- *Keep 4 bits and drop a message.* Rejected: every type in the list is load-
  bearing.

**Cost.** One bit of head-flit payload. The head flit uses 37 of 128 payload
bits, so the field grows into padding that already exists and no flit, buffer, or
link gets wider. The cost is genuinely zero; the only reason it needs recording
is that it is a documented departure from the spec's packet table.

---

## D3. Home-bank bits sit immediately above the block offset

**Decision.** `addr[6:5]` selects the home L2 bank, directly above the 5-bit
block offset.

**Why.** Consecutive lines land on different banks, so a sequential sweep
distributes across all four directories instead of hammering one. Since a tile's
home bank is a pure function of the address, this also means most requests
genuinely traverse the network rather than being served by the local bank —
which is the entire point of placing an L2 bank in every tile.

**Alternative considered.** *Bank bits above the index (`addr[14:13]`).* Each
bank would then own a large contiguous region. Rejected: a linear sweep through
an array would sit on one bank for 8 KB at a time, serializing every request
through one directory and one network destination, and the NoC would be idle on
three of four paths. Contiguous-region banking only pays off when you want
locality between a tile and its home bank, which requires the allocator to be
bank-aware — out of scope here, and not what a coherence testbench should be
exercising.

**Cost.** No spatial locality between a requester and its home bank: a tile's own
sequential accesses are spread over all four directories, so the average request
takes more hops than it would under region banking. This is the intended
behaviour — the design wants network traffic — but it does mean the measured
average latency here is pessimistic relative to a bank-aware allocator.

---

## D4. The L2 tag omits the bank bits

**Decision.** L2 index is `addr[12:7]`, L2 tag is `addr[31:13]`, 19 bits. The
home-bank field is not stored.

**Why.** A bank is only ever the home for addresses whose bank field selects it,
so within one bank those two bits are constant. Storing them would be storing a
constant in every one of the 512 tag entries.

**Cost.** The L2 tag is only meaningful relative to its bank instance; a raw tag
value cannot be turned back into an address without knowing which bank it came
from. Any debug bus or checker that reports L2 tags must therefore report the
bank id alongside, and the coherence checker does.

---

## D5. PIPT, not VIPT — and it is forced, not chosen

**Decision.** The L1 is physically indexed, physically tagged.

**Why.** Index+offset spans `addr[12:0]`, 13 bits. A 4 KB page offset is 12 bits,
so index bit `addr[12]` is a bit that address translation can change. Indexing
with it before translation is not an option, so the lookup is physical.

**Note on the general rule**, since this is a standard follow-up: VIPT requires
index+offset ≤ page offset. At 4 KB pages and 32-byte lines that caps a VIPT L1
at 128 sets (7 index bits + 5 offset bits = 12), i.e. 4 KB per way. Going beyond
that needs higher associativity (more ways, same number of sets), page colouring,
or explicit alias detection. The RTL does not claim VIPT anywhere.

**Cost.** Translation is on the critical path of the L1 lookup. Since this design
has no MMU or TLB, the cost is hypothetical here — but it is the reason the
sizing is worth stating rather than leaving implicit.

---

## D6. Reset is synchronized exactly once, in the top

**Decision.** `system_top` instantiates a single `reset_sync` (async assert,
sync de-assert) and fans its output to everything. No other module synchronizes
reset.

**Why.** Reset de-assertion has to meet recovery/removal timing at every flop it
reaches. Synchronizing once means one path is timed against the clock at the last
synchronizer stage; synchronizing per-block means every block contributes its own
removal path and its own chance of a reset-domain-crossing bug where two blocks
leave reset on different cycles.

**Alternative considered.** *Fully synchronous reset.* Rejected: it needs a
running clock to take effect, so nothing is in a known state before the first
clock edge, which complicates bring-up and gate-level simulation.

**Cost.** Two flops, and a mandatory convention that has to be enforced by review
— which is why `scripts/check_style.sh` exists and why this is written down.

---

## D7. Two documented lint waivers, both scoped to `coh_pkg.sv`

**Decision.** `UNUSEDPARAM` and `UNUSEDSIGNAL` are waived for
`rtl/pkg/coh_pkg.sv` only.

**Why.** `UNUSEDPARAM` fires because the package declares the complete parameter
set at Phase 0 while most consumers do not exist yet. `UNUSEDSIGNAL` fires on the
address-decode helpers: a function that extracts one field from an address leaves
the other fields unread by construction, and the only way to silence it is to
pass pre-sliced arguments — which reintroduces exactly the magic numbers at call
sites that the helpers exist to eliminate.

**Cost, and the trap.** The `UNUSEDPARAM` waiver hides genuinely dead parameters
for the whole build. It must be deleted at the Phase 12 gate: if the design is
complete and the waiver is still needed, the parameters it covers are dead and
should be removed instead. That check is an explicit Phase 12 item, not a hope.

**Outcome at the Phase 12 gate.** The check paid for itself. Removing the
waiver turned up three parameters with no consumer anywhere -- `MEM_TILE_ID`,
`VC_PTR_W` and `SINK_BOUND` -- and all three are now deleted. `MEM_TILE_ID`
was the interesting one: it was dead because the implementation gives every
tile its own memory rather than routing fills to one controller, which is a
real deviation from the specification that nobody had written down until the
dead parameter pointed at it (see `docs/spec.md`, deviation 1).

The waiver did not disappear, though; it moved. A leaf module elaborated on its
own reads a handful of the package's parameters and no more, so in the
per-module lint pass every other parameter looks dead -- an artefact of the cut,
not a property of the design. That pass, and the testbench builds, which
elaborate subsets too, now take `lint/waivers_perfile.vlt`. The design-level
pass takes only `lint/waivers.vlt` and has no `UNUSEDPARAM` waiver at all,
which is where "this parameter has no consumer" is a real finding.

Splitting them is the part worth keeping: a waiver that is correct for one
elaboration and wrong for another should be scoped to the elaboration it is
correct for, not granted globally because it is inconvenient twice.

---

## D8. The FIFO refuses a write while full even when a read drains a slot

**Decision.** `wr_ready_o = !full_o`, with no dependence on `rd_ready_i`.

**Why.** Hard constraint 3 forbids a combinational path from `ready` back to
`valid` within a module. The "optimized" form `wr_ready_o = !full_o || do_rd`
creates exactly that path: the downstream consumer's `rd_ready_i` would feed
combinationally into the upstream producer's `wr_ready_o`, and two such FIFOs
back to back would compose that path across the whole chain. In a NoC where
every hop contains a buffer, that is how a design ends up with a critical path
proportional to the number of routers.

**Alternative considered.** *Allow the same-cycle bypass and cut the path with a
skid buffer at the boundary instead.* Rejected as the default: it moves the
problem rather than removing it, and it makes the FIFO's timing behaviour
dependent on how it was instantiated. `skid_buffer` exists for the places that
genuinely need a cut, and it is explicit about it.

**Cost, and the number that matters later.** One cycle of re-acceptance latency
on entry to full. Steady-state throughput with both sides active is still one
beat per cycle, because the FIFO settles at an occupancy of `DEPTH-1` where
`wr_ready_o` is continuously high. The consequence is that a `VC_DEPTH=4` buffer
sustains full-duplex traffic at an effective occupancy of 3, not 4, and that is
the figure that must be compared against the credit round-trip latency in the
Phase 3 sizing analysis -- not the nominal depth.

---

## D9. An output VC is released on the tail *credit*, not on the tail *send*

**Decision.** `router` marks an output VC busy when `vc_allocator` grants it, and
clears it only when the downstream returns the credit carrying `credit_tail`.

**Why.** The route for a packet is computed at buffer-write time and stored in
the downstream input VC's state (`vc_out_port_q`). That state is per-VC, not
per-packet, so it can hold exactly one packet's routing decision at a time. The
conventional rule -- free the output VC as soon as the tail is *sent* -- opens a
window: the upstream may allocate that VC to a new packet and send its head
while the downstream VC still holds the previous packet's flits and its routing
state. The head would overwrite `vc_out_port_q` mid-packet, and the remaining
body flits of the old packet would be switched to the new packet's output port.
That is silent misrouting, not a stall, and it would show up as "lost" flits far
from the cause.

**Alternatives considered.**

- *Per-flit route storage, or a small per-VC queue of routing decisions.* This is
  what a throughput-optimized router does, and it is the right answer at scale.
  Rejected here because it makes the input unit hold a variable number of
  packets, which multiplies the state space that the Phase 2 assertions have to
  cover, for throughput this design does not need.
- *Free the VC on tail send and forbid back-to-back allocation by convention.*
  Rejected: "by convention" is not checkable, and the failure mode is silent.

**Cost.** One credit round trip of extra occupancy on every output VC: the VC
sits allocated but idle from the moment the tail departs until the credit comes
back. With `VCS_PER_VNET = 2` the sibling VC covers the gap, so a single vnet can
still stream continuously, and the measured random-traffic runs show no stalls
attributable to it. At `VCS_PER_VNET = 1` this choice would roughly halve
per-vnet throughput, which is the honest statement of what it costs.

The mechanism is also why the credit return carries a `credit_tail` bit at all.
That bit is the only thing distinguishing "a buffer slot freed" from "the packet
is done", and the two must not be conflated.

---

## D10. L1 tags and coherence state live in flops; only the data lives in SRAM

**Decision.** `l1_cache` keeps `tag_q` and `state_q` as flop arrays (64 sets x 2
ways: 2688 bits of tag, 512 of state) and instantiates `sram_1rw` only for the
data array. This is a deliberate departure from the convention that every array
is an `sram_1rw`.

**Why.** From Phase 6 a snoop -- an `Inv`, a `Fwd-GetS`, a `Fwd-GetM` -- has to
look up a line's tag and state, and often modify the state, on a line the
pipeline may be accessing in the same cycle. With a single-ported tag SRAM that
is a structural conflict on *every* snoop, and the only ways out are to stall
the pipeline for each one, or to make the coherence response path lose
arbitration to the core. Stalling the pipeline on every snoop is how a cache
ends up unable to sink responses, which is exactly the condition the VN2
sink requirement forbids. A flop array has as many read ports as it has
readers.

It also makes the debug bus honest. The coherence checker needs `(state, tag)`
per way every cycle; reading that out of a single-ported SRAM would mean either
contending with the pipeline or a hierarchical reference into the array. With
flops it is a genuine module port that survives synthesis-style elaboration,
which is what the specification asks for.

**Alternatives considered.**

- *Duplicate snoop tag array.* This is what real designs do: a second copy of
  the tags, in SRAM, dedicated to the snoop port. Rejected only on scale -- at
  64 sets x 2 ways the duplicate would be the same 2688 bits, so the flop array
  *is* the duplicate, minus the coherence problem of keeping two SRAMs in step.
  At 512 sets x 8 ways the calculation inverts and the duplicated SRAM wins.
- *Single-ported tag SRAM with pipeline stalls on snoop.* Rejected: it couples
  snoop service time to core activity, and a busy core can then delay an
  `Inv-Ack` indefinitely. That turns a throughput choice into a liveness
  problem.
- *Dual-ported (1R1W) tag macro.* Rejected: substantially larger per bit than
  1RW, and it solves only the tag conflict while leaving the data array
  single-ported anyway.

**Cost.** About 3.2 kbit of flops per L1, roughly 12.8 kbit across four tiles,
against an SRAM implementation of the same bits. Flops are perhaps 6-10x the
area per bit of a compiled SRAM at this size, so the cost is real but bounded,
and at this capacity a 2688-bit SRAM macro would be inefficient anyway. The
data array, which is 16x larger and where the port pressure actually matters,
stays `sram_1rw` -- so the discipline of designing the pipeline around a
one-cycle, no-forwarding, single-port contract is preserved where it counts.

---

## D11. The MSHR does not merge secondary misses

**Decision.** One MSHR per line address, enforced by CAM. A second core request
to a line that already has a live MSHR replays until that MSHR retires.

**Why.** Merging means the MSHR must hold a list of waiting requests -- each
with its own word select, byte enables, store data and core tag -- and must
retire them all when the fill lands, in an order that preserves per-line program
order. That list is a queue inside every MSHR entry, it needs its own full/empty
handling, and it multiplies the state a protocol assertion has to cover. In a
design whose point is a defensible coherence protocol, spending that complexity
on a throughput optimization is the wrong trade.

Not merging also buys a property that matters more here than throughput:
**per-line program order is preserved for free.** Because a second request to a
line cannot be in flight while the first is, the golden model can be applied in
issue order and every response checked against it -- which is exactly what
`test_pipelined_random_against_golden` does. With merging, the testbench would
need its own model of the retirement order to know what each response should be.

**Alternatives considered.**

- *Full merge with a per-entry request queue.* The standard answer, and what a
  real design does: a load behind a load to the same line costs nothing. Its
  cost here is the queue, the retirement ordering, and a much larger reachable
  state space for the Phase 10 race tests.
- *Merge loads only, stall stores.* Cheaper than a full merge and captures most
  of the benefit, since load-load to the same line is the common case.
  Rejected on the same grounds, and because the asymmetry is another case split
  in the state table.

**Cost.** A secondary miss to a hot line pays a full memory round trip of
replay latency instead of riding the first fill. For the false-sharing test
(R10) that is precisely the traffic pattern, so the measured transactions per
store there will be pessimistic relative to a merging design. That is worth
saying out loud when quoting the R10 number.

---

## D12. An MSHR reserves its victim way, and invalidates the victim immediately

**Decision.** Allocation picks a victim way that no other live MSHR has
reserved -- replaying if none is free -- and sets that way's state to `I` in the
same cycle, capturing the dirty line and its address into the MSHR.

**Why, part one: reservation.** With `MSHR_ENTRIES = 4` and `L1_WAYS = 2`,
pseudo-LRU flips back after two allocations, so a third concurrent miss to one
set would be handed a victim way that an earlier MSHR already owns. Two fills
would then target the same way and one line would be silently lost. Pseudo-LRU
is a replacement *policy*; it is not an allocator, and treating it as one is the
bug.

**Why, part two: immediate invalidation.** This is the subtler half, and it was
bug B6. Leaving the victim valid until its fill arrives looks harmless -- the
line is still correct, so why not keep serving hits from it? Because a store
that hits it writes the array and sets the line dirty, while the writeback
already queued carries the copy captured at allocate time. The fill then
overwrites the way. The store is lost twice: once because the writeback carried
stale data, and again because the fill discarded the array copy.

**Consequence that must be handled.** Invalidating the victim means a new
request for the evicted line *misses* and allocates its own MSHR while the
writeback may still be queued. Its fetch must not overtake that writeback, or it
reads pre-writeback memory. The memory engine therefore masks a fetch whose line
address matches any live MSHR's pending `wb_addr`. This is also why the MSHR
stores `wb_addr` explicitly rather than reconstructing it from the tag array:
once the victim is invalidated, the way may be refilled and the tag no longer
describes the line being written back.

**Alternatives considered.**

- *Keep the victim valid and re-capture the writeback data at writeback time.*
  Needs an extra array read port, or arbitration against the pipeline, for the
  read. Rejected: it reintroduces exactly the single-port contention D10 is
  about, to preserve a handful of hits on a line that is about to disappear.
- *Keep the victim valid but refuse stores to it.* A read-only state for
  "resident but condemned". Rejected: it is a fourth stable state to carry
  through the coherence tables in Phase 6 for no protocol reason.

**Cost.** Hits to a condemned line are lost -- accesses between allocation and
fill miss and allocate a second MSHR, so a line evicted and immediately
re-referenced costs two memory round trips instead of one. The replacement
policy pays for this by not choosing recently-used ways, so the case should be
rare; the measured stress runs are where that assumption gets tested.

---

## D13. The directory blocks head-of-line on VN0, and that is safe

**Decision.** `dir_ctrl` processes one request at a time and does not pop the
VN0 head until that request commits. A request that hits a line in `S_D` is
left at the head and retried, so everything behind it waits too.

**Why it is safe, which is the part that matters.** This is the single most
dangerous construct in the design, and it is only safe because of three things
that have to hold together:

1. **VN0 stalling cannot block VN1 or VN2.** The three virtual networks have
   physically separate queues all the way through, so a wedged request queue
   cannot stop a forward or a response.
2. **Every stall is waiting on a VN2 message that is guaranteed to arrive.** A
   line is in `S_D` because the directory sent a `Fwd-GetS` to an owner that is
   obliged to answer with data. VN2 is a true sink, so that answer cannot be
   blocked by the very queue it will unblock.
3. **VN2 is serviced with absolute priority at the directory.** The arriving
   data is processed ahead of the stalled request, so the `S_D` resolves and the
   head makes progress on its next retry.

Remove any one of the three and this deadlocks. Assertions back all three: the
TBE liveness bound in `tbe_file` fires if anything waits longer than
`TBE_TIMEOUT`, and `a_vn2_never_backs_up` fires if the response queue ever fills.

**Alternative considered.** *Per-line stall: skip a blocked request and serve
the next one whose line is free.* This is what the specification prefers, and
it is the right answer for throughput. It needs the directory to search its
input queue rather than read its head, which means a CAM over queued addresses
against the live TBE set, and it introduces the possibility of reordering two
requests from the same core to different lines. Rejected for Phase 6 because
the blanket stall is provably safe with three short arguments, and the per-line
version needs a fourth about ordering.

**Cost.** One request blocked in `S_D` stalls every request behind it at that
bank, for the duration of a `Fwd-GetS` round trip. With four cores and four
banks the measured 10k-request run still completes in 25,667 cycles, so the
cost is real but not pathological at this scale. At 64 tiles it would be the
first thing to fix.

---

## D14. An eviction is its own MSHR transaction

**Decision.** When a core request needs to evict a victim, the victim gets its
own MSHR entry (`is_evict`) carrying `MI_A`/`EI_A`/`SI_A`/`II_A` and its data,
and the requesting access replays to allocate the now-free way separately.

**Why.** A victim in `MI_A` is not merely "data waiting to be written back" --
it is a line with a live coherence transaction that can still receive
`Fwd-GetS` and `Fwd-GetM` for its own address, and must answer them. That means
it needs an address a forward can CAM-match against, and a state a forward can
be applied to. Folding it into the requesting line's MSHR would give one entry
two addresses and two states, and the `MI_A + Fwd-GetM -> II_A` arc would have
to be applied to the right one.

It also produces a clean division that makes the rest of the controller
simpler: **the tag array owns the coherence state of every line that is
resident or becoming resident, transient states included, and an MSHR owns the
state only of a line that is leaving.** The two sets are disjoint because a
request to a line with a live MSHR replays, so a line can never be both.

**Alternatives considered.**

- *One MSHR with a victim sub-state.* Rejected above: two addresses in one
  CAM-matchable entry.
- *Wait for the Put-Ack before allocating the new line.* Simpler, and wrong in
  a way that matters: the Put-Ack is bookkeeping on the *old* line and says
  nothing about the new one. Waiting for it would add a full directory round
  trip to every conflict miss for no correctness benefit.

**Cost.** A conflict miss consumes two MSHR entries, so the effective miss
parallelism under conflict pressure is `MSHR_ENTRIES / 2` rather than
`MSHR_ENTRIES`. That is the honest figure to quote for a conflict-heavy
footprint, and it is why `test_l1_single_core.py` checks four *non*-conflicting
misses when it measures peak occupancy.

---

## D15. E is an ownership state, so its eviction is not silent

**Decision.** Evicting a line held in E sends `PutE` and waits for a `Put-Ack`
in `EI_A`. It is not dropped silently.

**Why.** The directory cannot tell whether a core holding E has upgraded to M,
because that upgrade is deliberately silent -- and making it silent is the
entire reason E exists. So when the directory records a line as E at core A, it
knows exactly one thing: A may or may not have modified it. If A could drop the
line without saying so, the directory would keep believing A is the owner
forever, and the next requester would be forwarded to a core that no longer has
the line.

The two silences are therefore not symmetrical, and the asymmetry is the point:
**the upgrade is silent because nobody needs to know; the eviction cannot be,
because the directory does.**

**Alternative considered.** *Silent E eviction, with the directory recovering by
timeout or by treating a forward to a non-holder as a miss.* This is what a
protocol without a PutE transaction has to do. Rejected: it turns a clean
protocol arc into an error-recovery path, and error-recovery paths are the ones
that never get tested.

**Cost.** One extra transaction on every eviction of a clean exclusive line,
which is the common case for read-only data that gets replaced. That is a real
throughput cost, and it is the price of the read-then-write saving E buys on the
other side. The `PutE` also has to exist as a distinct message type from `PutM`
so the directory can tell "clean, take nothing" from "dirty, take this data" --
which is why the message set has both.

---

## D16. The directed race tests force interleavings with a delay hook, never with waiting

**Decision.** `tb/harness/msg_delay.sv` sits on each source's outgoing path and
holds one message for a programmable number of cycles. A race test sets delays
to force the exact order it needs.

**Why.** A race reproduced by waiting longer has not been reproduced. Race R5 --
a Put arriving after ownership has moved -- cannot happen at all unless the Put
is delayed past a request from another core, and on a direct connection with
round-robin arbitration it simply never happens by chance. Without the hook the
test would pass without ever exercising the arc, which is worse than not having
the test.

The hook is in the harness rather than in `rtl/` so that forcing an interleaving
never involves changing the design under test.

**Why depth one.** Each element holds exactly one message and backpressures its
source behind it. A deeper element would let a later message from the same
source overtake an earlier one, which is not an ordering the protocol is
required to survive, and it would make failures depend on queue occupancy --
that is, unreproducible.

**Cost.** One cycle of latency on every message even when the delay is zero,
since the element registers its payload. That is uniform across all sources, so
it shifts absolute timings without changing any relative order, and the hook can
therefore be left in place for every test rather than being a special build.

---

## D17. Back-invalidation reuses the Fwd-GetM and Inv arcs, and adds no L1 state

**Decision.** When the L2 must free a way, it recalls the line from the caches
using messages that map onto arcs the L1 table already has: `MSG_RECALL` is
decoded as `EV_FWD_GETM` (owner case) and `MSG_RECALL_INV` as `EV_INV` (sharer
case). The L1 gains no new state, no new event column and no new transition.

**Alternatives.** A data-carrying `Inv` -- one message that both invalidates and
extracts the line -- would have been one message type instead of two. A
dedicated `Recall`/`RecallAck` pair with its own column in the L1 table would
have made the cache's behaviour explicit at the cost of 13 new cells.

**Why.** From the cache's point of view a recall is *exactly* a Fwd-GetM: give
up the line, hand over the data, go to I. Every transient case the table already
handles -- a recall arriving while the line is in `MI_A`, or in `SM_AD` waiting
for acks -- is handled correctly for free, and correctly for the same reason.
Thirteen new cells would each have been a place to get one of those cases wrong,
and the directed tests for the transient ones are the hardest to write.

A data-carrying `Inv` was rejected because it collapses two different things --
"stop using this line" and "send me your copy" -- into one message whose meaning
depends on the receiver's state. That is the kind of type the sharer case then
has to answer with an empty payload.

**Cost.** Two message types rather than one, and a directory that must know
which of the two replies to expect (`WB-Data` from an owner, `Recall-Ack` from a
sharer) rather than counting one uniform response. That cost is paid in
`dir_ctrl`, in a single counter, and not in the protocol table.

---

## D18. The in-tile consumer of a VN2 response is a function of its message type

**Decision.** A tile hosts both an L1 and a directory bank, and both receive VN2
responses. `tile_nic` decides which one an arriving response is for by calling
`coh_pkg::vn2_consumer_is_dir()` on its type -- nothing else. A recall therefore
gets its own acknowledgement type, `MSG_RECALL_ACK`, rather than reusing
`MSG_INV_ACK`.

**Alternatives.** (a) Carry an explicit destination-agent bit in the head flit
and let the sender say where the message is going. (b) Have the network
interface look the address up -- if this tile is the home bank for it, the
message is for the directory. (c) Offer the message to the L1 and fall back to
the directory if no MSHR matches.

**Why.** (b) is wrong outright: a cache in a home tile requests lines it is the
home for, so the address does not distinguish the two agents. (c) makes delivery
depend on a lookup in the receiver's state, which turns a routing question into
a race -- and the fallback path is precisely what the `a_vn2_always_lands`
assertion exists to forbid.

(a) is defensible and is what a larger design would do. It was rejected because
it puts a field in every flit to disambiguate exactly one message type, and
because the sender setting it correctly is then an invariant with no single
place to check. The type-based rule is one function, and its correctness
condition is a sentence: *no VN2 message type has two possible consumers*. Bug
B15 was that sentence being false, and once it is written as a function in the
package, the network interface and the direct-connect harness cannot drift apart
about it.

**Cost.** One extra message type per ambiguous case, forever. If a later phase
adds a response that both agents can receive, it must be split in two rather
than tagged -- and the pressure on the 5-bit type field is real (23 of 32
encodings are now used). The alternative was one bit in every flit; this is one
encoding per case, and it is the cheaper trade while cases are rare.

---

## D19. Every race test asserts a witness before it asserts a value

**Decision.** Each of the twelve directed race tests asserts, from
`tb/models/probe.py`, that the exact `(state, event)` pair it is named after
was presented to the controller. Only then does it check values. The probe
reads the controllers' own decision-point signals -- `vn1_state` / `vn1_event`
at the cache, `old_meta_q.dir_state` / `dir_event` at the directory -- and
records an event when it is *presented*, not when it is accepted.

**Alternatives.** (a) Check outcomes only, and rely on mutation testing to
prove the test is doing work. (b) Add coverage points in RTL and read them at
the end. (c) Reconstruct the interleaving from a message trace in the
testbench.

**Why.** (a) is the status quo of most protocol testbenches and it is not
enough, for a reason this project has a name for: race R5 passed for three
phases while never producing the message it was named after (bug B17). Mutation
testing would eventually have caught it -- and did, the moment it was run --
but a mutation run is minutes and a witness is one line, and the witness says
*which* interleaving failed to happen rather than just that something is wrong.
The two are complements: the witness proves the test reached the race, the
mutation proves the race mattered.

(c) was rejected because a testbench-side reconstruction is a second
implementation of the protocol's message semantics, and the two drift. Reading
the controller's own inputs cannot drift: if the probe and the controller
disagree about what event this is, the probe is reading the wrong wire and
every test fails loudly.

**Presented, not accepted, and that distinction is load-bearing.** A cell whose
action is `stall` is never accepted -- `vn1_ready_o` stays low -- so a probe
that recorded accepted events would silently miss every stall arc, which is
half the catalogue. R6 (`IM_AD + Fwd-GetM -> stall`) failed its witness for
exactly this reason before the probe was fixed. Recording on `valid` also makes
the probe agree with the RTL's own legality assertion, which samples the same
condition.

**Cost.** A stalled message presents the same pair every cycle, so consecutive
repeats have to be collapsed, and a "count" is then a number of distinct
presentations rather than of cycles -- a subtlety that has to be documented
where the counts are used (R6, R10). The probe also reads hierarchical
internals, which is exactly what `docs/decisions.md` D-for-debug-bus argues
against for the *checker*. The difference is the audience: the SWMR checker is
a correctness monitor that must survive elaboration, while the probe is a
debugging instrument for twelve specific tests, and pinning it to internal
signal names is acceptable there because those tests are rewritten whenever the
controller is.

---

## D20. Liveness bounds are assertions inside the design, and their margin is measured

**Decision.** Both ends of every transaction carry a liveness bound as an SVA
assertion: `mshr_file` asserts no entry stays valid longer than `MSHR_TIMEOUT`
(1000 cycles), `tbe_file` the same with `TBE_TIMEOUT` (500). Both bounds are
module parameters. The age counters they read are declared inside
`` `ifndef SYNTHESIS `` and are not fields of `mshr_e` or `tbe_e`.

**Alternatives.** (a) A testbench watchdog: give up after N cycles with nothing
outstanding having completed. (b) One bound, at the directory only. (c) Keep
the age in the transaction structure, as the TBE file originally did.

**Why not a watchdog.** A deadlock does not produce a wrong value; it produces
silence. A watchdog fires long after the fact, in the wrong process, and says
only that something did not finish. The difference is not theoretical -- it was
measured. With the VC allocator's eligibility filter removed, R12 failed with:

```
AssertionError: 16 operation(s) still outstanding after 40000 cycles
```

and after the MSHR bound was added, the same run failed with:

```
mshr_file.sv: entry 0 for line d1c has been live 1000 cycles, exceeding the
liveness bound -- whatever it is waiting for is not coming
```

naming the tile, the entry and the line, on the cycle the bound was crossed,
400,000 cycles earlier than the watchdog would have.

**Why both ends.** The directory-only bound existed for three phases and did
not fire for the deadlock above, because for that traffic pattern the stuck
transactions were at the caches (bug B18). "The TBE bound covers it" is only
true for deadlocks the directory is part of.

**Why parameters.** Raising the bound is how a suspected deadlock is told apart
from a slow path: if the run completes with the bound at ten times the default
it was latency, and if it still fires it was a deadlock. That measurement is
what settled bug B14, where the worst TBE age went from 8000 (the raised bound,
still firing) to 54 after the fix.

**Why the counter is not in the struct.** It exists only so an assertion can
read it. A synthesizable structure carrying a field the design never reads is
an invitation for somebody to use it, and then the verification counter is
load-bearing logic.

**Cost.** A bound is a number, and a number that is too tight is a flake
generator. So the margin is reported, not assumed: every stress test prints the
worst age it observed against the bound. Under R12's deliberately adversarial
hold that is 315 of 1000 at the cache and 212 of 500 at the directory -- a 3x
and 2x margin, which is thin enough to be worth watching and is why it is
printed on every run rather than checked once.

---

## D21. A mutation table, and R10 has no entry in it

**Decision.** `scripts/mutations.py` lists one or two single-line RTL
mutations per race, each naming the test that must then fail.
`scripts/mutate.py` applies them one at a time, runs only that test, restores
the file, and reports any mutation the test failed to notice. `make mutate` is
part of the gate. Race R10 deliberately has no mutation.

**Alternatives.** (a) Trust that a passing test suite means the design is
tested. (b) Generate mutations automatically -- flip operators, drop
statements -- as academic mutation testing does. (c) Mutate and run the *whole*
suite for each.

**Why.** (a) is what the project believed until R5's witness failed. (b)
produces mostly equivalent or trivially-fatal mutants and buries the signal;
what is wanted here is not a mutation score but a specific claim -- *this test
is sensitive to this arc* -- and that requires hand-chosen mutations that
delete a protocol arc rather than corrupt an expression. (c) would make the
suite twelve times slower for no extra information: the claim is about one test
and one arc, and other tests failing too is expected and uninteresting.

**Why R10 has no entry.** R10 is the false-sharing storm. It is not a
correctness property -- nothing about it is incorrect, and there is no arc that
handles it. Its value is the number it reports (0.97 ownership transfers per
store for data that is never shared). Inventing a mutation for it would be
claiming coverage that does not exist, so the table records the gap explicitly
and `make mutate` prints it as `n/a` with the reason.

**Cost.** The table is hand-maintained and each entry names exact source text,
so a refactor of the state tables breaks it. The runner turns that into a loud
`BROKEN` verdict -- it requires the text to appear exactly once -- rather than
a silent skip, which is the failure mode that would matter. It also edits `rtl/`
in place, so it refuses to start if those files have uncommitted changes:
"restored" has to mean restored.

---

## D22. A packet keeps one virtual channel for its whole path

**Decision.** The VC index a packet uses within its virtual network is a
function of the sending tile -- `src_vc_id(t) = t[VC_ID_W-1:0]` -- and it does
not change at any hop. The network interface injects on that channel and waits
if it is busy; the router's VC allocator offers the input VC's own index as the
only candidate. An assertion, `a_same_vc`, says a grant never moves a packet
between channels.

**Alternatives.** (a) Lowest free VC, chosen per hop -- what this was. (b) VC
as a function of the *destination*. (c) Leave the network unordered and make
the protocol tolerate reordering.

**Why.** The coherence protocol requires point-to-point ordering on the forward
network and did not have it (bug B19). The requirement is easy to miss because
it is about two different message *classes*: the directory sends a cache a
forward, and later, on processing that cache's Put, a Put-Ack. The forward is
legal in `MI_A`; after the Put-Ack the cache is in `I`, where the table is
blank. The cell is blank because a correct directory never sends a forward
*after* an ack -- a statement about sending order that says nothing about
arrival order.

(c) was rejected because tolerating it means adding arcs for forwards into `I`,
and a cache in `I` has no data to answer with: the requester would wait forever
regardless. There is no correct permissive behaviour to add.

(b) orders correctly too, but funnels every packet bound for a tile onto one
channel, so it collapses to a single VC per vnet at the final hop. Keying on
the *source* keeps both channels in use everywhere and still gives the property
that is actually required, which is ordering per (source, destination) pair --
nothing in the protocol cares about the order of messages from different
senders.

**Why it stays deadlock-free.** Each VC index becomes an independent network,
each XY-routed, and XY routing is deadlock-free with a single buffer class. The
virtual networks still break the protocol-level dependency cycle; the VC index
now breaks nothing and orders everything.

**Cost, measured.** Channel utilisation. A packet that finds its channel busy
waits, where before it could take the other one. Re-running the mesh
load-latency sweep before and after gives **10-15% mean latency below the
knee** -- 8.2 to 9.2 cycles at 0.25 offered load, 8.7 to 10.1 at 0.40 -- and
**no change at saturation**: accepted throughput is 0.601 flits/cycle/node
against 0.604 before, because the limit there is the ejection port and the
allocator, neither of which this rule touches. The full table is in
`docs/noc_perf.md`.

The alternative was a protocol that loses a transaction whenever a Put-Ack
overtakes a forward, which the stress tier hit roughly once in ten thousand
requests. Not a close call -- but worth being able to say what was paid.

---

## D23. The core pipeline yields to both coherence paths, on the cycle

**Decision.** A request in S1 does not complete -- no hit response, no MSHR
allocation, no eviction -- if the forward path or the response path is acting
on the same line and way in that cycle. It replays.

**Alternatives.** (a) Give S1 priority and make the coherence paths retry. (b)
Order the writes inside the `always_ff` so the coherence path wins, and let S1
complete against the state it read. (c) Detect the collision and forward the
new state to S1 combinationally.

**Why.** Three signals write a line's coherence state -- S1, the VN1 handler,
the VN2 handler -- and each computes its next state combinationally from the
current one. Two writes at the same edge do not merge; the later one in the
block wins, and the other transition is silently lost. Bug B20 is that,
three times over: a store hit losing a Fwd-GetS's downgrade, an eviction
reading a pre-downgrade state, and a load hit overwriting the SM_A that an
arriving Data had just installed.

(a) is wrong at the protocol level, not just inconvenient: the response path is
a sink and must never be asked to wait, and a forward that retries indefinitely
is the deadlock the virtual networks exist to prevent. (b) is worse than
losing the transition -- it would let a *store* complete on a line that is
being given away, which is a coherence violation rather than a lost update.
(c) is the general answer and the expensive one: a bypass network between three
writers, each of whose next state depends on the others'.

Replaying is the cheap and correct option because the retry is not a
degradation. A store that finds its line downgraded to S on the retry issues a
GetM, which is exactly what a store to a shared line is supposed to do.

**Why the condition is `valid` and not `accepted`.** A forward can sit at the
input for several cycles while the handler finishes an earlier one, and S1
touching the line during that window is the same bug with a wider gap. Keying
on acceptance would also close a combinational loop, since acceptance depends
on retirement, which depends on S1.

**Cost.** A core request that collides with coherence traffic on its own line
loses a cycle. That is rare by construction -- the coherence path is busy with
a given line for a handful of cycles per transaction -- and it cannot livelock:
a forward is accepted within a bounded number of cycles, being blocked only by
a response, a retirement or an array write, none of which a replaying request
can sustain.

---

## D24. The stress tier raises the liveness bounds, and prints the margin

**Decision.** The constrained-random tier builds with `TBE_TO = 20000` and
`MSHR_TO = 40000` rather than the defaults of 500 and 1000, and every run
prints the worst age it actually observed against them.

**Alternatives.** (a) Keep the default bounds and inject smaller delays. (b)
Keep the default bounds and accept that the tier cannot reach the races that
need long delays. (c) Remove the bounds from the stress tier.

**Why.** The stress tier injects holds of up to 200 cycles on requests, because
the stale-Put family of races needs a request delayed long enough for a line's
directory state to move on twice -- hundreds of cycles, not tens. Measured
under those holds, a transaction legitimately lives around 600-900 cycles. The
default bounds, sized for a network without injected delay, then fire on
correct behaviour.

That was established by measurement rather than assumed: the bounds were raised
and the run repeated, and it completed with a worst MSHR age of 885 and a worst
TBE age of 687. A bound that fires at 500 against a real worst case of 687 is
not a deadlock detector, it is a flake.

(a) would have cost three directory arcs -- the whole E-state stale-Put family
went from uncovered to covered when the holds were lengthened. (c) would give
up the only mechanism that distinguishes a deadlock from a slow run, and the
racing configuration found a real deadlock (B20's third manifestation) that
nothing else would have caught.

**Cost.** The stress tier does not validate the default bounds. Those are
validated where they belong -- in the directed tiers, which inject no long
holds and report their own margins: race R12 measures 315 cycles against the
1000-cycle MSHR bound and 212 against the 500-cycle TBE bound.
