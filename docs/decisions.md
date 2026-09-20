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
