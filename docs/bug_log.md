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
