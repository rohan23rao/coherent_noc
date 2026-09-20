# Bug log

One entry per bug, in the order found. Each entry must be readable by someone
who has not seen the waveform:

- **Symptom** — what failed, and what the failing output actually said.
- **Localization** — how the search was narrowed. This is the part that matters.
- **Root cause** — stated in protocol or microarchitecture terms, not "typo on
  line 88". A typo is how the bug got in, not what the bug was.
- **Fix** — what changed and why that is the right place to change it.
- **Test that catches it now** — by name. A bug without a regression test is
  still open.

A bug found by inspection rather than by a failing test still gets an entry, and
the "test that catches it now" field still has to be filled in.

---

## B1. Four unit tests silently mis-timed their own stimulus (Phase 1, testbench)

**Symptom.** Four of five Phase 1 unit tests failed on first run, each in a way
that looked like a different RTL bug:

```
credit_counter  cycle 3: rtl=3 model=4 (send=0 ret=1)
fifo            out of order: [160, 160, 161, 162]
skid_buffer     assert [0, 1, 3, 3, 4, 5, ...] == [0, 1, 2, 3, 4, 5, ...]
```

A duplicated FIFO head, a skid buffer apparently dropping beat 2 and
duplicating beat 3, and a credit counter off by one. Three different modules,
three different-looking failures.

**Localization.** The common factor was that all three failing tests drove an
input and then immediately awaited a clock edge, while the one passing
randomized test (`rr_arbiter`) did not depend on exactly which edge its
stimulus landed on. That pointed at the testbench's timing model rather than at
three unrelated RTL bugs, so rather than guess, a throwaway probe measured the
behaviour directly against `credit_counter`, whose state is a single integer:

```
after reset:                                credits=4
drove send=1, awaited ONE edge:             credits=4     <-- not sampled yet
awaited a SECOND edge (send still 1):       credits=3     <-- sampled here
```

**Root cause.** In cocotb 2.x a value written with `sig.value = x` is applied
*after* the next awaited edge, so the DUT does not sample it at that edge but at
the following one. Every test written as `drive(); await RisingEdge()` therefore
applied its stimulus one cycle later than it accounted for. In `credit_counter`
the stimulus stayed asserted across two edges and was counted twice; in `fifo`
the `rd_ready_i` that was supposed to pop the head did not take effect until a
cycle later, so the head was sampled twice; in `skid_buffer` the same skew made
the testbench's notion of which beats fired disagree with the DUT's.

This was a testbench defect, not an RTL defect. Worth logging anyway, because
it is the failure mode that most easily produces a false RTL bug report: three
modules all looked broken, and the instinct to start editing the RTL would have
introduced real bugs into correct code.

**Fix.** Added `tbutil.step()`, which advances one edge and then offsets a
nanosecond past it, giving ordinary cycle semantics: after `step()`, registered
outputs are settled and any value driven is sampled at the next edge. Every
test was converted to `sample-then-drive` around `step()`. A second, related
defect was fixed at the same time: `test_random_backpressure_preserves_order`
read back `dut.dn_ready_i`, a signal the testbench itself drives, and got the
stale value. Testbenches now use the local Python variable instead of reading
back their own outputs.

**Test that catches it now.** All five Phase 1 unit tests, which fail
immediately if `step()` is reverted to a bare `RisingEdge`. `step()` is the
single point where the idiom is defined, so the whole suite regresses together
rather than one test at a time.

---

## B2. A unit test asserted the opposite of its module's contract (Phase 1, testbench)

**Symptom.** `test_simultaneous_read_write_while_full` failed with
`count moved on simultaneous rd+wr: 3`, expecting the FIFO to accept a write in
the same cycle a read drained a slot while full.

**Localization.** Read the RTL rather than the waveform: `wr_ready_o = !full_o`,
and `do_wr = wr_valid_i && wr_ready_o`. There is no path by which a concurrent
read can admit a write in the same cycle, and the module header says so
explicitly.

**Root cause.** The test, not the design. Admitting the write would require
`wr_ready_o = !full_o || do_rd`, which creates a combinational path from
`rd_ready_i` to `wr_ready_o` -- precisely the path a ready/valid cut exists to
break, and forbidden by hard constraint 3.

**Fix.** Rewrote the test to assert the real contract: while full, `wr_ready_o`
stays low even with a concurrent read; the write is accepted on the following
cycle; and no data is lost across the sequence. The behavioural cost is recorded
as decision D8, because the effective-occupancy number it implies is an input to
VC buffer sizing in Phase 3.

**Test that catches it now.**
`test_unit_fifo.py::test_full_refuses_write_even_with_concurrent_read`, which
now fails in the other direction -- if someone "optimizes" `wr_ready_o` to
depend on `rd_ready_i`, the test fails on the `refused` assertion.

---

## B3. Credit return was combinational, so observers saw it a cycle early (Phase 2, RTL)

**Symptom.** Four of five standalone router tests failed on an in-RTL assertion
rather than on a testbench check:

```
input_unit.sv:193: Assertion failed in
  router.gen_input_unit[2].u_input_unit.gen_vc_asserts[0].a_head_only_when_idle:
  head flit arrived at VC 0 in state 2 -- the upstream reallocated an output VC
  before this VC drained
```

State 2 is `VC_ALLOC`: a head arrived at a VC that already owned an output VC
and was mid-packet.

**Localization.** The testbench harness deliberately mirrors the router's own VC
discipline -- it will not start a packet on an input VC until the DUT returns
that VC's *tail* credit -- so either the harness was releasing a VC too early or
the DUT was signalling the release too early. Comparing the two, the harness
samples all DUT outputs just after the clock edge, which is correct for a
registered output and wrong for a combinational one. `credit_valid_o`,
`credit_vc_o` and `credit_tail_o` were driven from an `always_comb` block keyed
on `sa_grant_i`.

**Root cause.** The credit return was combinational, so it asserted during the
cycle the tail was *being read*, and by the time the clock edge had passed it
already reflected the next cycle's switch-allocation decision. Any observer
sampling post-edge -- the harness, and equally a real upstream router that
registers its credit counter -- therefore saw the tail credit associated with
the wrong cycle, and released the output VC one cycle before the input VC had
actually drained. This also violated hard constraint 3: a credit return crosses
a link, so it must be registered, and combinationally it would have put the
upstream's credit counter directly on this router's allocation critical path.

**Fix.** Registered the three credit-return outputs in `input_unit`. The flit
select `sa_flit_o` stays combinational, and is documented as such, because it
feeds the crossbar inside the same pipeline stage. The cost is one extra cycle
of credit round-trip latency, which is now an input to the Phase 3 buffer-depth
analysis rather than an unmodelled assumption.

**Test that catches it now.** `input_unit`'s own `a_head_only_when_idle`, which
is what found it, exercised by
`test_noc_router.py::test_random_traffic_no_loss_or_duplication` and
`::test_slow_credit_return_throttles_but_never_drops`. Reverting the credit
return to combinational makes both fail within a few hundred cycles.

**Worth noting.** The assertion that caught this was written in the same commit
as the bug, for a condition I expected to be structurally impossible. It fired
on the first run of real traffic. That is the argument for writing the
"can't happen" assertions rather than reasoning that they cannot happen -- the
same argument the spec makes for R8.

---

## B4. A store returned the pre-store word on a hit and the post-store word on a miss (Phase 4, RTL)

**Symptom.** The randomized Phase 4 run disagreed with the golden model on the
sixth operation:

```
op 5: store to 0x6200 returned 0x0, golden says 0x18e00000
```

The five directed tests before it -- including a store followed by a load of the
same word, and a store surviving eviction and refill -- all passed.

**Localization.** The disagreement was on the value a *store* returns, not on
any subsequent load, so the line itself was correct in the array; only the
response word was wrong. Two paths produce a store response: the hit path in S1
(`core_resp_rdata_o <= s1_rword`) and the fill path in `M_FILL`
(`core_resp_rdata_o <= miss_fill_line[...]`). The fill path merges the pending
store into the line before selecting the word; the hit path selected from
`s1_hit_line`, which is the line *before* the merge.

**Root cause.** Two code paths implementing the same architectural event had
drifted apart: a store miss returned the post-store word, a store hit returned
the pre-store word. The directed tests missed it because none of them checked
the value a store itself returns -- they checked what a subsequent *load*
returned, and the array was being written correctly in both cases. Only the
randomized test, which compares every operation's response against the golden
model including stores, could see it.

**Fix.** Defined the store response as the word *after* the store, in one place:
`s1_rword` now selects from `s1_merged` when the operation is a store and from
`s1_hit_line` otherwise. That makes the hit path agree with the fill path rather
than the other way round, because the post-store value is the more useful of the
two -- it lets a scoreboard confirm a store landed without issuing a load, which
matters once stores can be outstanding.

**Test that catches it now.**
`test_l1_blocking.py::test_random_against_golden`, which checks the response of
every operation including stores. The directed tests deliberately were not
changed to cover it: the lesson is that a scoreboard comparing *every* response
against a reference finds things a targeted test does not, and the suite should
keep demonstrating that.

---

## B5. The replay slot dropped its occupant whenever it could not reissue (Phase 5, RTL)

**Symptom.** Phase 5 tests hung waiting for responses that never came:

```
AssertionError: 1 responses never arrived: [4]
AssertionError: 1 responses never arrived: [2]
```

Always exactly one request, always under MSHR pressure or way-conflict
pressure, and never the same tag twice.

**Localization.** Timing out on a response means the request is either stuck or
gone; the MSHR assertions were quiet, so it was not stuck in an MSHR. Dumping
the pipeline state each cycle made it immediate:

```
c8  s1v=0 rpl=1 rpladdr=00200 mshrv=1111 fill_gv=1 ready=0
c9  s1v=0 rpl=0 rpladdr=00200 mshrv=1110 fill_gv=0 ready=1
```

At c8 the request is parked in the replay slot and the array is busy with a
fill, so it cannot be reissued. At c9 the slot is simply empty and the request
has not gone anywhere -- `s1_valid_q` is still 0.

**Root cause.** Two defects in the same mechanism.

First, `rpl_valid_q` was cleared unconditionally at the top of the sequential
block and only re-set by a *replaying S1 request*. On any cycle where the slot's
occupant could not be issued into S0 -- because a fill or a committed S2 store
owned the data array -- nothing re-set it, and the request evaporated.

Second, and found while fixing the first: the slot holds one request, but two
could be in the replay path at once. A new core request could be accepted into
S1 on the same cycle the request already in S1 replayed; when the new one later
replayed in turn, it overwrote the slot's occupant.

**Fix.** The slot now holds its occupant until that request is actually
reissued (`if (rpl_valid_q && s0_issue) rpl_valid_q <= 1'b0;`), and
`core_req_ready_o` additionally refuses a new request on any cycle where the S1
request is about to replay. The second change makes the invariant
`s1_replay |-> !rpl_valid_q` hold, which is now asserted as
`a_replay_slot_not_overwritten` -- so the overwrite variant cannot come back
silently.

**Test that catches it now.**
`test_l1_nonblocking.py::test_mshr_full_stalls_then_serves` and
`::test_concurrent_misses_to_one_set_do_not_double_book`, both of which create
sustained replay pressure. The invariant assertion catches the overwrite variant
directly.

---

## B6. A store to a line already being evicted was lost twice (Phase 5, RTL)

**Symptom.** The pipelined randomized run disagreed with golden on a load:

```
tag 3: got 0x0, golden says 0x81 [LD addr=0x4284 op#42]
```

A byte-enabled store to that word had completed 40-odd operations earlier and
been acknowledged. Every directed Phase 4 and Phase 5 test passed.

**Localization.** The value read was memory's zero, not the stored byte, so the
store had been lost rather than reordered. It was a load *hit* returning stale
data or a *miss* refetching a line whose store never reached memory. Both point
at the eviction path, and the address was in a set the test deliberately
oversubscribes. Reading the allocation path with that in mind made it clear
that the victim line stays `valid` from the moment an MSHR reserves its way
until the fill lands.

**Root cause.** A window between allocation and fill in which the victim is
condemned but still serving. A store landing in that window hits the victim,
writes the data array and marks the line dirty. Meanwhile the MSHR already holds
the copy of the line captured at allocation -- before the store -- and that is
what the writeback sends to memory. The fill then overwrites the way. The store
is lost twice over: the writeback carried the pre-store copy, and the fill
discarded the post-store copy.

The directed tests missed it because they evict lines they are finished with.
Only randomized traffic stores to a line during the window in which it is being
replaced.

**Fix.** The victim is invalidated at allocation, in the same cycle its data
and address are captured, so it can no longer be hit. That created a second
ordering requirement, handled at the same time: because the line is now absent,
a new request for it misses and allocates its own MSHR, and that MSHR's fetch
must not overtake the still-queued writeback. The memory engine masks a fetch
whose line address matches any live MSHR's pending `wb_addr`. The MSHR carries
`wb_addr` explicitly for this reason -- reconstructing it from the tag array is
no longer safe once the way can be refilled.

**Test that catches it now.**
`test_l1_nonblocking.py::test_pipelined_random_against_golden`, which stores
into a footprint chosen so that eviction and re-reference overlap. The directed
tests were deliberately not extended to cover it: the point worth keeping is
that a window this narrow is found by randomized traffic against a reference
model, not by cases a designer thinks to write down.

---

## B7. The directory wrote the previous transaction's metadata (Phase 6, RTL)

**Symptom.** Three cores shared a line; a fourth core's store invalidated only
one of them. The message trace showed the directory sending a single `Inv` with
`ack=1` when two sharers were recorded:

```
c1  L12->VN0 GETM a=0x24 s=2 d=0 r=2 ack=0
c5  D0->VN2  DATA_DIR a=0x24 s=0 d=2 r=2 ack=1     <-- ack=1, should be 2
c6  D0->VN1  INV a=0x24 s=0 d=0 r=2                <-- only tile 0 invalidated
```

**Localization.** `ack=1` and one `Inv` are both derived from the same value:
`old_meta.sharers & ~requester`. So the directory believed there was one
sharer. Tile 1's earlier GetS had been answered -- the trace shows its Data --
so the request was processed; only its effect on the sharer vector was missing.
That narrows it to the metadata write, not the decision logic.

**Root cause.** The metadata write port was driven with `new_meta_q`, a
*register* loaded at the end of the same `D_EXEC` cycle in which the write is
issued. At the moment of the write it still held the previous transaction's
result. Every metadata update was therefore applied one transaction late, so
tile 1's `add_sharer` was written during some later, unrelated request -- or
not at all.

The combinational `new_meta` and the registered `new_meta_q` differed by one
cycle and by one character at the use site, which is exactly the kind of
difference that reads as correct.

**Fix.** `D_EXEC` writes the combinational `new_meta`; `D_WRDATA`, which runs
the cycle *after* `D_EXEC`, writes `new_meta_q`. Both are commented with which
one is correct where, because the two are otherwise indistinguishable by eye.

**Test that catches it now.**
`test_protocol_msi.py::test_store_invalidates_sharers`, which shares a line
between two cores before storing from a third and then requires that no sharer
survives. The 10k randomized run also catches it, via SWMR.

---

## B8. IM_A and SM_A never became M when the ack count reached zero (Phase 6, RTL)

**Symptom.** The store that had just correctly invalidated its sharers never
completed. Tracing showed the data arriving with `ack=1`, the `Inv-Ack` arriving
from the sharer, and then nothing.

**Localization.** The MSHR was in `IM_A` with `ack_cnt == 0` and `data_valid`
set -- everything the transaction was waiting for had arrived. The retirement
condition required a *stable* state, and `IM_A` is not stable, so the entry sat
there forever.

**Root cause.** The specification's table says "IM_A on Inv-Ack: `ack--`; if 0
-> M", and `l1_coh_fsm` implements only the `ack--` half, with a comment saying
the controller drives the rest. The controller never did. The table is a pure
(state, event) function and cannot see the ack count, so this promotion is
genuinely the controller's job -- it was simply missing.

**Fix.** The controller now computes the post-update ack count and promotes
`IM_A`/`SM_A` to M when it reaches zero, in the same cycle as the update rather
than at retirement, so the promotion is visible to a forward arriving on the
next cycle. Doing it here also covers the early-ack race R1 in one place: when
acks overtook the data, `ack_cnt` is already negative and the arriving AckCount
credits it straight to zero, so `IM_AD + Data[ack>0]` promotes to M immediately
without ever resting in `IM_A`.

**Test that catches it now.**
`test_protocol_msi.py::test_store_invalidates_sharers` (which requires the
store to complete at all) and `::test_random_with_swmr_checker`.

---

## B9. A request that triggered an eviction was dropped (Phase 6, RTL)

**Symptom.** A load hung after a conflicting store had filled the set:

```
=== ST 0x80 ===          ... completes, line in M
=== LD 0x2080 ===        ... completes, line in S
=== LD 0x4080 ===        forces an eviction
  L10>VN0 PUTM a=0x4     eviction issued
  D0>VN1  PUT_ACK a=0x4  eviction completes
  LD completed: False    ... and the load never went out
```

The eviction worked perfectly. The request that caused it disappeared:
`s1_valid_q=0`, replay slot empty, no MSHR, and no GetS ever sent.

**Localization.** The trace makes this one immediate -- a PutM went out and a
Put-Ack came back, so the eviction path was healthy, while no GetS followed. In
S1 a request can take exactly one of four exits: respond, allocate, evict, or
replay. `s1_replay` was written as
`s1_valid_q && !s1_resp && !s1_evict && !s1_alloc`, so on the cycle it took the
evict exit it did not also take the replay exit, and `s1_valid_q <= s0_issue`
cleared it.

**Root cause.** Treating "evict" as if it served the request. It does not:
issuing a Put frees the way, and the original load or store still has to be
performed against the now-free way. The eviction is a *side effect* of the
request, not a disposition of it.

**Fix.** `s1_replay = s1_valid_q && !s1_resp && !s1_alloc`. The rule is now
"replay unless the request was answered or got its own transaction", which is
the correct statement and does not enumerate exits.

**Test that catches it now.**
`test_l1_single_core.py::test_dirty_victim_survives_eviction`, and every later
test with a footprint that overflows a set -- including the 10k randomized
multi-core run, where it was originally masked by the run stalling for a
different reason.

---

## B10. The network interface starved one of its two response feeders (Phase 8, RTL)

**Symptom.** Found by inspection while chasing B11 and B12, not by a failing
test -- which is why it is worth logging.

**Root cause.** A tile has two sources of VN2 responses: its L1 (data forwarded
to a requester, Inv-Acks) and its directory (data to a requester). The NIC
selected between them with `vn2_pick_l1 = l1_vn2_valid_i`, i.e. strict priority
to the L1. Any tile whose L1 has a continuous stream of responses starves its
own directory indefinitely, and a directory that cannot send is one whose
requesters' MSHRs age out.

The reasoning that produced the bug is in the comment it replaced: "an L1
response can be what a directory is waiting on, never the other way round."
That is true and irrelevant. Both sides carry responses that some *other* node
is blocked on, so neither may be permanently deferred, regardless of which one
can block which.

**Fix.** A round-robin arbiter between the two feeders.

**Test that catches it now.** Nothing directly, and that is stated honestly:
the randomized network run would need a traffic pattern that keeps one L1's
response path permanently busy. The starvation is bounded by the arbiter's
fairness, which is measured in `test_unit_rr_arbiter.py`, and the TBE and MSHR
liveness assertions are what would catch a regression -- eventually.

---

## B11. Ejection across all VCs by lowest index deadlocked the tile (Phase 8, RTL)

**Symptom.** The randomized run over the network tripped the directory's TBE
liveness bound:

```
tbe_file: entry 0 for line 110 has been live 500 cycles, exceeding TBE_TIMEOUT
```

**Localization.** The aged TBE was in `S_D`, waiting for a `Fwd-GetS` response.
Dumping the L1 states for that line showed the requester in `IS_D` and tile 2
still in `M` -- so the owner had never acted on the forward.

**Root cause.** The NIC reassembles into one slot per incoming VC, then picked
ONE completed message per cycle across all six slots by lowest index. VN0's VCs
are indices 0 and 1, VN1's are 2 and 3, VN2's are 4 and 5. A directory blocked
head-of-line -- an ordinary and expected condition -- leaves its VN0 slot
occupied, and that slot then permanently outranks every VN1 and VN2 slot behind
it.

That is not a fairness problem, it is a deadlock: the VN2 response stuck behind
the VN0 request is precisely what would have unblocked the directory. It is
also exactly the failure the specification warns about in requiring the NIC to
"present independent ready/valid per VNet in both directions so a blocked VN0
cannot stall VN2", and the mistake was collapsing three logical paths into one
selector for convenience.

**Fix.** Three independent ejection paths, one per virtual network, each with
its own selection, its own destination and its own accept. A blocked VN0 now
blocks only VN0.

**Test that catches it now.**
`test_protocol_noc.py::test_random_over_network`, via the TBE liveness
assertion. Reverting to a single selector reproduces it within a few thousand
cycles.

---

## B12. A tail credit was dropped when a body flit arrived in the same cycle (Phase 8, RTL)

**Symptom.** After fixing B11 the randomized network run still aged out a TBE,
at the same point. Dumping every tile showed something stranger than a stall:

```
tile0 NIC rx_full=000000 rx_busy=000000 pk=000 inj_req=000 vcbusy=011101
tile1 NIC rx_full=000000 rx_busy=000000 pk=000 inj_req=000 vcbusy=000001
tile2 NIC rx_full=000000 rx_busy=000000 pk=000 inj_req=000 vcbusy=000011
```

Every queue empty, every packetizer idle, nothing in flight anywhere -- and
output VCs still marked busy. Tile 0 had both of its VN1 VCs marked busy, so
its directory could never send another forward; tile 2 had both VN0 VCs, so it
could never send another request.

**Localization.** "Busy but nothing in flight" says the release mechanism
failed, not the traffic. An output VC is released when the credit for its tail
comes back. So a tail credit had gone missing -- one per stuck VC, accumulating
until a vnet ran out of VCs and that tile's path wedged for good.

**Root cause.** The NIC generated at most one credit per cycle through a single
write port:

```
if (flit_valid_i && !flit_i.tail)  credit = body credit for the arriving flit;
else if (ej_any_accept)            credit = tail credit for the freed slot;
```

Two credits can become due in the same cycle -- a body flit lands on one VC
while a previously assembled message is taken from another -- and the `else if`
silently discarded the second. Under light traffic the coincidence is rare,
which is why every directed test passed; under randomized load it happens
often enough to exhaust a vnet's VCs within a few thousand cycles.

The `a_credit_not_dropped` assertion present at the time checked that a credit
offered to the queue was accepted. It could not fire, because the dropped
credit was never offered.

**Fix.** Credits are accumulated per VC -- a count of outstanding body credits
and a tail flag -- and one is emitted per cycle, tails first because a tail
frees a whole VC while a body frees one slot. Nothing is discarded; credits can
only be late.

**Test that catches it now.**
`test_protocol_noc.py::test_random_over_network`, plus two new assertions that
would have caught it directly: `a_pending_body_bound` (a VC can owe at most
`VC_DEPTH` body credits) and `a_tail_credit_not_lost` (an accepted message must
leave a tail credit pending or sent on the next cycle). The second is the one
that matters -- it asserts the property the old code violated, rather than the
property the old assertion happened to check.

---

## B13. An assertion on a signed field compared it unsigned (Phase 8, testbench-in-RTL)

**Symptom.** The network run tripped
`a_ack_never_positive_after_data`: "credited an AckCount onto an
already-positive count -- Data arrived twice". No data had arrived twice.

**Root cause.** The assertion read `mshr[vn2_idx].ack_cnt <= '0`. `ack_cnt` is
declared `logic signed`, but it is a member of a packed struct, and a member
read out of a packed struct does not reliably carry its own signedness -- the
struct as a whole is unsigned. So a two's-complement `-1` compared as `15`, and
the assertion fired on exactly the early-ack case the design is built around:
Inv-Acks overtaking the Data that carries the AckCount, which is race R1 and
the single most load-bearing detail in the protocol.

**Why the protocol was unaffected.** Every functional use of `ack_cnt` is
either arithmetic, which is correct in two's complement regardless of
signedness, or a test against zero, which is sign-agnostic. Only the comparison
inside the assertion was wrong. An assertion that fires on correct behaviour is
worse than no assertion: it trains you to discount it.

**Fix.** `$signed(mshr[vn2_idx].ack_cnt) <= 0`, with a comment saying why the
cast is load-bearing rather than decoration.

**Test that catches it now.** `test_protocol_noc.py::test_random_over_network`,
which produces early acks routinely under load.

---

## B14. The VC allocator starved every other VC behind a blocked one (Phase 8, RTL)

**Symptom.** After B10, B11 and B12 the network still deadlocked. Raising the
TBE liveness bound to 8000 cycles showed it was a genuine deadlock, not
latency: the bound was reached rather than approached.

**Localization.** A full dump at the stall told the story in three lines:

```
R0 out_vc_busy = ... L:000011          (both LOCAL VN0 output VCs busy)
R0 in L: state=[1,0,1,1,0,0] outport=['L','L','E','S',...]
tile0 NIC rxfull=110000                (both VN0 reassembly slots full)
```

Tile 0's directory was stuck mid-send, so it could not drain its VN0 input --
ordinary, expected backpressure. But its router's LOCAL input also held two VN1
packets (VCs 2 and 3, state ROUTED) bound for ports E and S, and both of those
ports had every output VC free. Traffic that could move was not moving.

**Root cause.** `vc_allocator` is separable input-first: stage one picks one VC
per input port, stage two arbitrates per output VC. The stage-one arbiter's
round-robin pointer only advances when a grant is actually *taken*. VC 0 of the
local input -- a VN0 packet whose output port had no free VN0 VC -- won stage
one every cycle, produced no candidate, lost stage two, and therefore never
advanced the pointer. VCs 2 and 3 were never even offered.

So a blocked VN0 stalled VN1 and VN2 *inside the allocator*, which defeats the
entire purpose of separate virtual networks: the responses that would have
unblocked the directory were the ones being starved. Every structural
separation elsewhere -- separate queues in the NIC, separate ejection paths,
separate credits -- was undone by one shared arbiter.

**Fix.** Eligibility is computed for every input VC before stage one runs: a VC
whose output port has no free VC in its own vnet does not compete. A blocked VC
now simply does not request, so the pointer advances past it and the VCs behind
it are served.

**Fix verified by measurement, not by the absence of a failure.** With the
bound raised to 8000 the run now completes all 10,000 requests and the worst
TBE age observed is **54 cycles** -- two orders of magnitude below the 500-cycle
default, which is therefore left unchanged. Before the fix the same measurement
hit 8000.

**Test that catches it now.**
`test_protocol_noc.py::test_random_over_network`, via the TBE liveness
assertion. This is the bug that most justifies having that assertion at all:
nothing else in the suite would have distinguished "deadlocked" from "slow".

---

## B15. An Inv-Ack for a recall was delivered to the wrong agent in its own tile (Phase 9, RTL)

**Symptom.** `test_protocol_inclusion.py::test_back_invalidation_of_a_shared_line`
tripped an L1 assertion rather than failing a check:

```
[10841001] %Error: l1_cache.sv:1023: Assertion failed in
  system_top.gen_tile[0].u_tile.u_l1.a_vn2_always_lands:
  l1_cache: tile 0 got a VN2 response for line 8 with no MSHR
  -- the response has nowhere to go
```

The dirty-victim case (R9 proper) passed. Only the *shared* victim failed, and
only in tile 0.

**How it was localized.** Line 8 is address `0x100`, the shared victim; its
home bank is `addr[6:5] == 0`, so the directory doing the recall lives in
**tile 0**, and tile 0's L1 was one of the two sharers. That coincidence is the
whole bug. The recall sends an invalidation to each sharer naming the directory
as requester, so tile 0's L1 correctly answered `Inv-Ack` addressed to tile 0 --
and the message came back to the tile that had to decide which of its two
agents should receive it.

**Root cause.** `tile_nic` classified an arriving VN2 response by message type:

```systemverilog
assign vn2_out_to_dir = (ej_msg[2].msg_type == MSG_WB_DATA);
```

That is a *function of the type*, which is the right shape of rule -- the
network interface sees a flit, not a transaction, and has no way to ask who is
waiting for it. The defect is that back-invalidation broke the property the
rule depends on. Until Phase 9 every `Inv-Ack` answered a *cache* that was
acquiring the line, so "Inv-Ack goes to the L1" was sound. A recall makes the
*directory* the thing being acknowledged, and now one type has two possible
consumers. Every Inv-Ack for a recall went to the L1, which had no MSHR for a
line it was not requesting.

Note what did *not* go wrong: routing between tiles was correct, and the
directory's ack counter simply never decremented. Had `a_vn2_always_lands` not
existed, the symptom would have been a TBE liveness timeout hundreds of cycles
later, in a different module.

**Fix.** Restore the invariant instead of special-casing the tile. Back
-invalidation of a sharer now sends `MSG_RECALL_INV`, which maps to the same
`EV_INV` arc in the L1 table, and the L1 answers it with `MSG_RECALL_ACK`
addressed to `home_of(addr)`. The classification moved into `coh_pkg` as
`vn2_consumer_is_dir()` so the network interface and the direct-connect harness
cannot disagree, and so that "the consumer of a VN2 message is determined by
its type alone" is written down in one place -- see decision D18.

**Test that catches it now.**
`test_protocol_inclusion.py::test_back_invalidation_of_a_shared_line`. It is
specifically a *shared* victim whose home bank is co-located with one of its
sharers; a victim in M never exercises the path, because that reply is WB-Data,
which was already classified correctly.

---

## B16. A forward read its state from the wrong place when a miss was outstanding (Phase 10, RTL)

**Symptom.** The very first run of the R2 test -- two sharers store to one line,
the loser takes an Inv while upgrading -- killed the simulation:

```
[1220000] %Error: l1_cache.sv:1026: Assertion failed in
  system_top.gen_tile[0].u_tile.u_l1.a_vn1_event_legal:
  l1_cache: tile 0 took VN1 event 5 in state 0, which the table marks
  impossible -- if this is a forward into II_A it is race R8 and the bug is
  at the directory
```

Event 5 is Inv and state 0 is I: the cache claimed to have been shown an Inv
for a line it did not hold.

**How it was localized.** The arc probe gave the whole interleaving for free,
and it was the *expected* one -- dir I+GetS, tile 0 IS_D+DataE, dir E+GetS,
tile 0 E+Fwd-GetS, dir S_D+Data, dir S+GetM, tile 1 SM_AD+Data. Nothing was out
of order. Meanwhile a per-cycle dump of the debug bus showed tile 0 sitting in
**SM_AD** for that line right up to the cycle the assertion fired. So the state
the controller reported and the state the array held disagreed, which narrows
it to the three lines that choose between them. Dumping the VN1 message and the
two selectors confirmed it: correct address, correct set and tag, and
`vn1_in_array = 1` *and* `vn1_in_mshr = 1` at once.

**Root cause.** The coherence state of a line being forwarded to lives in one
of two places. For a line being **evicted** it lives in the MSHR, because the
array entry has already been invalidated. For everything else, transients
included, it lives in the array. The selector preferred the MSHR:

```systemverilog
vn1_match[i] = mshr[i].valid && (mshr[i].addr == vn1_msg_i.addr);
...
assign vn1_state = vn1_in_mshr ? mshr[vn1_idx].state : ...
```

but the match was on address alone, so it also hit the **fetch** MSHR that any
outstanding miss has for exactly that address. A fetch entry never writes its
`state` field -- the array is authoritative -- so it reads as I, and every
forward arriving at a cache with a miss outstanding on the same line was
classified against state I. The comment above the code asserted the two were
disjoint and gave a reason ("a request to a line with a live MSHR replays");
that reason is about *resident versus being evicted* and does not cover
*resident with a fetch in flight*, which is the ordinary case for every
transient state.

It also corrupted the write side. `vn1_in_mshr` gates the MSHR update at the end
of a forward, so a forward handled in SM_AD would have written the next state
into the fetch entry's unused state field instead of the array.

Why nothing caught it earlier: the constrained-random driver enforces one
outstanding operation per line, so no forward ever reached a cache that had a
miss outstanding on that same line. Every transient-state forward arc in the
protocol -- the entire left half of the race catalogue -- was unreachable from
the random tests by construction. This is the bug that justifies the directed
race tier existing at all.

**Fix.** Restrict the match to eviction entries, which is what the selector
meant all along:

```systemverilog
vn1_match[i] = mshr[i].valid && mshr[i].is_evict &&
               (mshr[i].addr == vn1_msg_i.addr);
```

Both uses -- reading the state and writing the next state -- are then correct
for the same reason, and the disjointness argument in the comment becomes true
as written.

**Test that catches it now.** `test_races.py::test_r2_upgrade_loses_the_race`,
and R3, R4, R6 and R8 would all catch it too: any race whose arc is a forward
into a transient state.

---

## B17. A race test that passed without ever exercising its race (Phase 10, testbench)

**Symptom.** `scenario_r5` -- the PutE-from-a-non-owner case, part of the
Phase 7 and Phase 8 gates -- had passed since the day it was written. The first
time it was run under the arc probe it failed immediately:

```
AssertionError: the interleaving never happened: bank 0 was never shown
  PutE(non-owner) in any state. tile 0's PutE was not delayed past tile 1
  taking the line, so the owner check was never exercised.
```

**How it was localized.** The witness names the missing event, so there was
nothing to localize: the directory had never seen a PutE from a non-owner, in
any state, at any point in the run.

**Root cause.** The scenario used two addresses, `a0` and `a0 + 8192`, and
relied on the second access evicting the first. They share an L1 set -- but the
L1 is **two-way**, so they landed in different ways and nothing was ever
evicted. With no eviction there was no PutE, and with no PutE there was nothing
for the owner check to get wrong. Every assertion in the test was about the
final values, and those values were correct for reasons that had nothing to do
with the arc in the test's name.

The deeper cause is the shape of the test, not the arithmetic. A test that
checks only outcomes cannot distinguish "the machine handled this correctly"
from "this never happened". Both look like a pass.

**Fix.** Three lines in one set, so the third access forces the first out, and
a wait on a named condition -- tile 0's MSHR reaching `EI_A` -- rather than a
fixed number of cycles. Then every race test in the catalogue asserts a witness
from the probe before it asserts anything about values, and `make mutate`
checks the other direction: delete the arc and the test must fail.

With the scenario fixed, `mutate --race R5` kills it, and `PutE(non-owner)` is
observed at the directory in state M.

**Test that catches it now.** The witness inside
`test_races.py::test_r5_pute_from_non_owner` catches it directly, and the
mutation `r5-drop-put-owner-check` catches it from the other side. The same
repair applies to the copies of this scenario in the Phase 7 and Phase 8 gates,
which share the body.

---

## B18. No liveness bound on the MSHR, so a cache-side deadlock was silent (Phase 10, missing assertion)

**Symptom.** With the VC allocator's eligibility filter reverted -- bug B14 put
back deliberately as the R12 mutation -- the R12 test failed like this:

```
AssertionError: 16 operation(s) still outstanding after 40000 cycles
```

That is the testbench giving up. No assertion fired anywhere in the design.

**How it was localized.** The specification calls for two liveness bounds:
every TBE retires within `TBE_TIMEOUT`, every MSHR within `MSHR_TIMEOUT`.
`TBE_TIMEOUT` and `MSHR_TIMEOUT` were both defined in `coh_pkg`, and grepping
for the second one found exactly one hit: its own definition. Only the
directory end had ever been checked.

**Root cause.** The deadlock B14 produces is at the *cache* end for this
traffic pattern: four caches with all four MSHRs waiting on responses that the
allocator is starving. The directory's TBEs were not the ones stuck, so the
only bound that existed did not fire, and the failure surfaced as a testbench
timeout hundreds of thousands of cycles later.

A testbench watchdog is not a deadlock detector. It fires long after the fact,
in the wrong process, and says only that something did not finish.

**Fix.** `mshr_file` now carries the same bound the TBE file does, with the
timeout as a module parameter so a suspected deadlock can be told apart from a
slow path by raising it. The same run now stops with:

```
mshr_file.sv:210: Assertion failed in
  system_top.gen_tile[2].u_tile.u_l1.u_mshr.gen_mshr_asserts[0].a_mshr_liveness:
  mshr_file: entry 0 for line d1c has been live 1000 cycles, exceeding the
  liveness bound -- whatever it is waiting for is not coming
```

naming the tile, the entry and the line, on the cycle the bound was crossed.

While adding it, the age counter moved out of `tbe_e` and into the
`ifndef SYNTHESIS` block of the file that reads it. It exists only for the
assertion, and a synthesizable structure carrying a field the design never
reads is an invitation for somebody to use it.

**Test that catches it now.** `make mutate RACE=R12`. The margin is reported
rather than assumed: under R12's deliberately adversarial hold the worst
observed MSHR age is 315 cycles against the 1000-cycle bound and the worst TBE
age 212 against 500.
