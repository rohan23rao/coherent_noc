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
