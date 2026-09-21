# The race catalogue

Twelve interleavings that a directory MESI protocol has to survive. For each
one: the interleaving as a sequence, the arc that handles it, the test that
drives it, and what happens when that arc is deleted.

Three things are true of every entry, and the third is the one that matters.

1. **The interleaving is forced, never waited for.** Each test sets a
   deterministic hold on one sender's messages through `tb/models/net_delay.py`
   and the `msg_hold` elements at the network interface. A race reproduced by
   waiting longer has not been reproduced -- it has been observed once, by
   luck, and the next run may not repeat it. No test in this file contains a
   settle delay, a sleep, or a magic number of cycles chosen by experiment:
   every wait is for a named condition.

2. **The interleaving is witnessed.** Before a test asserts anything about
   values it asserts that the exact table cell the race is about was presented
   to the controller, read from `tb/models/probe.py`. This is not decoration.
   Race R5 had passed since Phase 7 while never once producing the message it
   was named after -- see bug B17 -- and only the witness caught it.

3. **The test is shown to have teeth.** `make mutate` deletes each handling arc
   and requires the named test to fail. A test that still passes with its arc
   removed is testing nothing, and the mutation table is the evidence that none
   of these are in that state.

Running them:

```
make test TEST=races      # all twelve
make mutate               # delete each arc, require its test to fail
make mutate RACE=R3       # one race
make mutate MUT=r7-s-d-serves-gets
```

Notation in the diagrams: `C0..C3` are the four cores' L1s, `D` is the home
directory bank. Time runs downward. `|` marks a message that is being held in
the network by the test's delay hook.

---

## Summary

| # | Race | Test | Mutation | Result |
| --- | --- | --- | --- | --- |
| R1 | Early Inv-Ack | `test_r1_early_inv_ack` | `r1-drop-early-inv-ack`, `r1-unsigned-ack-cnt` | KILLED (both) |
| R2 | Upgrade loses the race | `test_r2_upgrade_loses_the_race` | `r2-drop-sm-ad-inv` | KILLED |
| R3 | Writeback vs forward | `test_r3_writeback_vs_forward` | `r3-drop-mi-a-fwd-getm` | KILLED |
| R4 | Stale PutS | `test_r4_stale_puts` | `r4-stale-puts-clears-owner` | KILLED |
| R5 | PutE from a non-owner | `test_r5_pute_from_non_owner` | `r5-drop-put-owner-check` | KILLED |
| R6 | Two GetMs back to back | `test_r6_two_getms_back_to_back` | `r6-drop-im-ad-fwd-stall` | KILLED |
| R7 | GetS into `S_D` | `test_r7_gets_into_s_d` | `r7-s-d-serves-gets` | KILLED |
| R8 | Forward into a dead MSHR | `test_r8_no_forward_into_a_dead_mshr` | `r8-owner-not-reassigned-on-forward` | KILLED |
| R9 | Back-invalidation hits M | `test_r9_back_invalidation_hits_m` | `r9-drop-recalled-dirty-data` | KILLED |
| R10 | False-sharing storm | `test_r10_false_sharing_storm` | none -- see below | n/a |
| R11 | Silent E->M then eviction | `test_r11_silent_upgrade_then_eviction` | `r11-drop-unexpected-putm-data` | KILLED |
| R12 | Virtual-channel starvation | `test_r12_blocked_vn0_does_not_stop_vn1_vn2` | `r12-vc-alloc-ignores-eligibility` | KILLED |

Twelve mutations, twelve killed. R10 has no mutation on purpose; the reason is
in its entry, and inventing an arc for it would be claiming coverage that does
not exist.

Three more mutations are not races at all. They are the invariants the
constrained-random tier found -- nobody thought of them in advance, which is
the point -- and each is paired with the stress configuration that found it:

| # | Invariant | Test | Mutation | Result |
| --- | --- | --- | --- | --- |
| B19 | a packet keeps its virtual channel, so messages between one pair of tiles cannot be reordered | `test_stress_16_lines` | `b19-lowest-free-vc` | KILLED |
| B20 | the core pipeline yields to the forward and response paths on a line they are acting on | `test_stress_racing_same_line` | `b20-s1-ignores-coherence-paths` | KILLED |
| B21 | a clean PutE restores the L2's "my copy is current" flag | `test_stress_l2_capacity_pressure` | `b21-pute-leaves-l2-stale` | KILLED |

Fifteen mutations, fifteen killed.

---

## R1 -- Early Inv-Ack

Inv-Acks from the sharers reach the requester before the Data message that
tells it how many to expect.

```
   C1(S)        C2(S)        C0(I)              D(S, sharers={1,2})
                             --- GetM -------------->
                             <================ Data+AckCount=2 |  (held 40)
     <------------------------------- Inv -----------
                  <---------------------------- Inv -----------
     --- Inv-Ack ------------->                            ack_cnt = -1
                  --- Inv-Ack ->                           ack_cnt = -2
                             <=== Data+AckCount=2 ===       ack_cnt =  0 -> M
```

**Forced by** holding the home bank's VN2 for 40 cycles. Only the *bank's*
responses are held: the sharers answer on VN2 as well, and a single VN2 control
would have delayed the very acks that are supposed to overtake. That is why the
delay hook has a separate channel for the directory's responses.

**Arc.** `ack_cnt` is a signed field, and `IM_AD + Inv-Ack` decrements it and
stays in `IM_AD`. Completion is `ack_cnt == 0 after the data has arrived`,
never `ack_cnt == 0`.

**Witness.** `IM_AD + Inv-Ack` observed at C0, and `ack_cnt` observed to go
negative. The run reports the minimum: **-2**.

**Mutations.**
- `r1-drop-early-inv-ack` removes the `IM_AD + Inv-Ack` cell. The table then
  calls the event impossible and the illegal-transition assertion fires.
- `r1-unsigned-ack-cnt` leaves the arc alone and breaks the arithmetic
  instead, so the count never reaches zero and the transaction never
  completes. Two different mutations for one race because the arc and the
  field width are two separate ways to get this wrong, and the test should be
  sensitive to both.

---

## R2 -- An upgrade loses the race

Two sharers both store. The directory orders one of them first, and the loser
takes an Inv while it is already in `SM_AD` waiting for its upgrade.

```
   C0(S)                      C1(S)                     D(S, sharers={0,1})
   --- GetM ---| (held 30)
                              --- GetM ------------------>
     <------------------------------------- Inv ----------  (C1's GetM first)
   SM_AD + Inv -> Inv-Ack, -> IM_AD
   --- Inv-Ack ------------->
   === GetM ===============================>             D is now M, owner=C1
                                            <-- Fwd-GetM --
                              --- Data ----->
```

**Forced by** holding C0's VN0 for 30 cycles. The hold is downstream of the
cache, so C0 is in `SM_AD` the whole time its GetM sits in the network -- which
is exactly the state the race needs.

**Arc.** `SM_AD + Inv -> send Inv-Ack, -> IM_AD`. It must ack, or the core that
won the race waits forever. And it must fall back to `IM_AD` rather than
staying in `SM_AD`: its shared copy is gone, so it now needs full data, not
just an AckCount.

**Witness.** `SM_AD + Inv` observed at C0.

**Mutation.** `r2-drop-sm-ad-inv` removes the cell; the illegal-transition
assertion fires.

**This race found a real bug.** The first run of this test died on an assertion
that had nothing to do with `SM_AD`: the cache reported state `I` for a line
the debug bus showed in `SM_AD`. A forward read its coherence state from the
MSHR whenever any entry matched the address, including the ordinary fetch entry
that every outstanding miss has. See bug B16 -- and note that the random tests
could not have found it, because their driver allows one outstanding operation
per line and therefore never lets a forward meet a miss on the same line.

---

## R3 -- Writeback versus forward

A cache evicts a dirty line; before its PutM reaches the directory, the
directory -- still recording it as owner -- forwards somebody else's GetM to it.

```
   C0(M)                                   D(M, owner=C0)          C1(I)
   evict: --- PutM ---| (held 200)
                                              <---------- GetM -----
     <---------------- Fwd-GetM ----------------
   MI_A + Fwd-GetM -> Data to C1, -> II_A
   --- Data ----------------------------------------------------> C1(M)
   === PutM =========================>        PutM from a NON-owner
     <---------------- Put-Ack -----------------
   II_A + Put-Ack -> I
```

**Forced by** holding C0's VN0 for 200 cycles, then waiting on the named
condition "C0's MSHR for this line is in `MI_A`" before issuing C1's store.

**Arcs**, two of them, and both are needed:
- `MI_A + Fwd-GetM -> send Data to the requester, -> II_A`. The cache still has
  the data; the PutM being in flight does not change that. It goes to `II_A`
  rather than `I` because its Put-Ack is still owed, and retiring the entry
  here would leave a message arriving with no state to receive it.
- At the directory, `M + PutM from a non-owner -> Put-Ack, no state change`.
  The owner is C1 now. Acting on C0's Put would disown C1.

**Witness.** `MI_A + Fwd-GetM` and `II_A + Put-Ack` at C0, and
`dir M + PutM(non-owner)` at the bank.

**Mutation.** `r3-drop-mi-a-fwd-getm` removes the first arc; C1's store never
gets its data.

---

## R4 -- Stale PutS

A sharer evicts. Before its PutS lands, another core takes the line for
writing.

```
   C0(S)                                   D(S, sharers={0,2})     C1(I)
   evict: --- PutS ---| (held 200)
                                              <---------- GetM -----
     <------------------- Inv ------------------
   SI_A + Inv -> Inv-Ack, -> II_A
   --- Inv-Ack ---------------------------------------------> C1(M)
   === PutS =========================>        D is M; the sharer vector is
     <------------------ Put-Ack ---------------  empty, so there is nothing
   II_A + Put-Ack -> I                          to remove and nothing to forget
```

**Forced by** holding C0's VN0 for 200 cycles and waiting for C0's MSHR to
reach `SI_A`.

**Arc.** `dir M + PutS -> Put-Ack, and no state change`. The temptation is to
treat a Put as authoritative -- it is, after all, a cache telling the directory
it no longer has the line -- but this one is about a copy the directory already
forgot, and acting on it clears an owner that has nothing to do with it.

**Witness.** `dir M + PutS` at the bank, and `SI_A + Inv` at C0.

**Mutation.** `r4-stale-puts-clears-owner` makes the directory clear the owner
and drop to `I`. C1's acknowledged store is then lost: the next reader misses
in the L2, fetches from memory, and gets the pre-store value.

---

## R5 -- PutE from a non-owner

The same shape as R4 with an exclusive line, and the one where the owner check
on a Put is load-bearing.

```
   C0(E)                                   D(E, owner=C0)          C1(I)
   evict: --- PutE ---| (held 250)
                                              <---------- GetM -----
     <---------------- Fwd-GetM ----------------             D: M, owner=C1
   EI_A + Fwd-GetM -> Data to C1, -> II_A
   --- Data ----------------------------------------------------> C1(M)
   === PutE =========================>        PutE from a NON-owner
     <---------------- Put-Ack -----------------  owner is untouched
```

**Forced by** holding C0's VN0 for 250 cycles, waiting on C0 reaching `EI_A`.

**Arc.** The directory splits `Put{M,E}` by whether the requester is the
recorded owner, and the non-owner cell acks without touching the owner field.

**Witness.** `PutE(non-owner)` observed at the bank; the run reports which
state it landed in (M, in practice, because C1's GetM has already been served
by then).

**Mutation.** `r5-drop-put-owner-check` removes the split in the directory's
event decode, so every Put looks like the owner's. C1 is disowned and its store
is lost.

**This race is also bug B17.** For three phases this test passed without ever
producing a PutE at all: it used two addresses in a two-way set and nothing was
ever evicted. The values it checked were right for reasons unrelated to its
name. The witness is what exposed that, and it is the strongest argument in
this file for having witnesses at all.

---

## R6 -- Two GetMs back to back

The directory hands the line on before the previous hand-off has finished, so
the second forward arrives at a cache that is still waiting for its data.

```
   C0(M)              C1(I)              C2(I)          D(M, owner=C0)
                      --- GetM -------------------------->   owner=C1
     <------------------------------ Fwd-GetM --------------
   --- Data ----| (held 60) ->
                                         --- GetM --------->   owner=C2
                      <------------------------ Fwd-GetM ---
                      IM_AD + Fwd-GetM -> STALL
                      === Data ===>  C1(M), store applied
                      --- Data ------------->  C2(M)
```

**Forced by** holding C0's VN2 for 60 cycles so C1 is still in `IM_AD` when the
second forward reaches it.

**Arcs.**
- At the directory, `M + GetM -> Fwd-GetM to the current owner, owner :=
  requester`. The owner changes when the forward is *sent*, not when the data
  lands. That is what serialises the two requests, and it is also why R8 is
  impossible.
- At the cache, `IM_AD + Fwd-GetM -> stall`. A cache cannot forward data it
  does not have. Stalling on VN1 is legal precisely because VN2 is a sink and
  the data is already on its way -- the stall is bounded by something that
  cannot itself be blocked.

**Witness.** Two or more `dir M + GetM` at the bank, and `IM_AD + Fwd-GetM` at
C1. Note that the probe records events that are *presented*, not only those
that are accepted: a stalled forward is never accepted, and half this
catalogue is about stall cells.

**Mutation.** `r6-drop-im-ad-fwd-stall` removes `EV_FWD_GETM` from the stall
list in `IM_AD`, and the table then calls the second hand-off impossible.

---

## R7 -- A GetS arrives at a directory in `S_D`

`S_D` means: the directory has forwarded a GetS to the owner and has not yet
received the data. Its own L2 copy is stale **by construction** -- the owner
wrote to the line without telling it.

```
   C0(M, wrote X)                          D(M, owner=C0)     C1(I)   C2(I)
                                              <---- GetS -------
     <---------------- Fwd-GetS ----------------           D: S_D
   --- Data to C1 ---| (held 80)
   --- WB-Data to D -| (held 80)
                                              <---------------------- GetS
                                              S_D + GetS -> STALL
   === WB-Data ======================>        D: S, L2 copy now current
                                              --- Data to C2 --------->
```

**Forced by** holding C0's VN2 for 80 cycles -- which delays both the data to
C1 and the writeback to the bank -- and waiting on the named condition "C0 has
been asked to downgrade" before issuing C2's load.

**Arc.** `dir S_D -> stall GetS and GetM, accept every Put`. The asymmetry is
deliberate: stalling the Puts as well would deadlock, because the cache that
owes this directory its data may itself be waiting on a Put-Ack from here.

**Witness.** `dir S_D + GetS` at the bank.

**Mutation.** `r7-s-d-serves-gets` makes `S_D` answer a GetS from the L2 copy.
The test's readers then see the value the owner overwrote, and nothing else in
the machine notices: no assertion fires, no message is lost, the protocol is
internally consistent and the data is wrong.

---

## R8 -- A forward into a dead MSHR

`II_A` means: this cache has given the line up, answered somebody's forward,
and is waiting only for the Put-Ack that retires the entry. A forward arriving
now would have no data to answer with.

**There is no arc.** The cell is blank and the RTL asserts on it, because the
case is impossible for a reason worth being able to state cold:

> The directory stops naming a cache the moment it forwards away from it. The
> owner field is updated when the Fwd-GetM is *sent*, not when the data lands
> (R6), and a requester is removed from the sharer vector *before* its Put-Ack
> is sent. So by the time a cache is in `II_A` -- which it can only reach by
> having answered a forward -- there is nothing left in the directory that
> could name it in another one.

If that assertion ever fires during stress, the bug is at the directory and
not a missing cache arc. The instinct to add a permissive state instead of
proving the case impossible is the thing to resist.

```
   C0(M)                       D(M, owner=C0)      C1        C2        C3
   evict: --- PutM ---| (held 250)
                                 <----- GetM ------           owner=C1
     <------- Fwd-GetM ------------
   -> II_A, Data to C1 ------------------------->
                                 <---------------------- GetS          owner
                                 --- Fwd-GetS to C1 ------->           moves
                                 <--------------------------------- GetM
                                 --- Fwd-GetM to C2 ------------------>
   (nothing is ever sent to C0)
   === PutM ====================>
     <------- Put-Ack -------------
```

**The test** drives the window as wide as it goes: C0 sits in `II_A` while two
more cores take the line in turn. The pass condition is that the only event
ever presented to C0 in `II_A` is its Put-Ack.

**Mutation.** `r8-owner-not-reassigned-on-forward` removes `set_owner_req` from
`dir M + GetM`, so the directory keeps naming C0 after forwarding away from it.
The next request is forwarded to a cache in `II_A` and the L1's
illegal-transition assertion fires -- which is the assertion doing exactly the
job it was written for.

---

## R9 -- Back-invalidation hits a line held in M

Capacity pressure in the *shared* cache destroying a *private* cache's dirty
line. The L2 is strictly inclusive, so freeing a way means removing the line
from every L1 first -- and if the victim is dirty somewhere, the recall is the
only thing carrying that data out.

```
   C0(M, wrote X)                      D (L2 set full, victim = C0's line)
                                       allocate TBE in INV_PEND
     <---------------- Recall -----------
   M + Fwd-GetM arc -> WB-Data to the HOME BANK, -> I
   --- WB-Data --------------------->    last response: write X to memory,
                                         then free the way
```

**Forced by** a deterministic footprint rather than a delay: C0 stores to the
line first so it lands in L2 way 0, seven more lines from the other tiles fill
ways 1..7 of the same set, and a ninth forces the eviction of way 0.

**Arc.** The recall reuses the `Fwd-GetM` arc at the cache (decision D17): the
cache gives the line up and answers with data exactly as it would to a real
forward. What differs is the reply's *address* -- WB-Data to the home bank, not
owner-data to a requesting cache -- and the directory writes it to memory
before freeing the way.

**Witness.** `M + Fwd-GetM` observed at C0, which is the reuse the design
depends on.

**Mutation.** `r9-drop-recalled-dirty-data` stops the directory capturing the
recall response's data, so it writes back its own stale L2 line instead. Every
store made since the line was handed out is lost, silently.

The sharer case -- the same recall against a line held in S -- is covered by
`test_protocol_inclusion.py::test_back_invalidation_of_a_shared_line`, which
is where bug B15 was found.

---

## R10 -- False-sharing storm

Four cores store to four **distinct words of one line**, round-robin. Nothing
here is incorrect and there is no arc to delete, which is why this row has no
mutation: a mutation table that invented one would be claiming coverage that
does not exist.

What the test reports is the cost:

> 32 stores to 4 disjoint words of one line cost **31 ownership transfers** and
> **31 cache-to-cache forwards** -- 0.97 transfers per store, for data that is
> never actually shared.

Every store but the first moves the line. The cores share no data at all; they
share a *cache line*, and coherence is line-granular. This is the number that
padding a structure to a cache-line boundary buys back, and it is worth being
able to quote.

The test still checks values: each core reads back its own word and finds what
it wrote. A ping-pong that also corrupts is a different bug, and the assertion
that the line really did ping-pong (at least `stores - 4` GetMs found the line
owned elsewhere) keeps the measurement honest -- without it, a run where the
line somehow stopped moving would report a flattering number and pass.

---

## R11 -- Silent E->M, then an eviction

A core in E may upgrade to M without telling anybody. That is the whole point
of E -- it is what makes a read-then-write cost one transaction instead of two.
The price is that the directory cannot know whether a line it recorded as E is
clean.

```
   C0: LD  -> E            D: I + GetS -> DataE, D: E, owner=C0
   C0: ST  -> M            (silent: zero messages)
   C0: evict -> PutM carrying data
                           D: E + PutM(owner) -> take the data, -> I
```

**Arc.** `dir E + PutM from the owner -> copy the data into the L2, ack,
-> I`. The directory must accept data for a line it believed was clean.

**Witness.** `dir E + PutM(owner)` at the bank.

**Mutation.** `r11-drop-unexpected-putm-data` removes `copy_data_l2`. The Put
is still acked and the protocol stays internally consistent; the store made
after the silent upgrade simply vanishes.

---

## R12 -- Virtual-channel starvation

The failure that three virtual networks exist to prevent, and the one that
survived every direct-connect test in the project.

A request that cannot make progress holds a virtual channel. If anything
shared -- an allocator, an ejection port, a credit pool -- lets that block what
is behind it, the responses that would have unblocked the request never arrive.
The dependency is circular and the machine stops.

**Forced by** three conditions that all have to hold at once:

1. Every line homed at **one** bank, so VN0 toward that bank saturates and its
   output virtual channels really are all busy.
2. Still enough distinct lines -- sixteen, across eight sets -- to keep every
   core's MSHRs full. The testbench allows one operation per line, so too small
   a footprint throttles the stimulus instead of the network, and a network
   that never fills cannot starve anything. An earlier version of this test
   used three lines and passed against a knowingly broken allocator.
3. One cache's responses held for 40 cycles. That keeps a TBE live at the
   bank, and the directory stalls VN0 head-of-line while a TBE is live -- which
   turns "VN0 toward this bank is busy" into "VN0 toward this bank is stopped".

The collisions matter as much as the load: they are what generate the VN1
forwards and VN2 acks that leave a tile through the same router input as its
blocked VN0.

**Arc.** The VC allocator computes eligibility before its first arbitration
stage: an input VC whose output port has no free VC *in its own virtual
network* does not compete. Without that, a blocked VC wins stage one every
cycle, produces no candidate, loses stage two, and never advances the
round-robin pointer -- so every VC behind it is never even offered.

**Detector.** Not a value check and not a testbench timeout: the liveness
assertions inside the design. With the mutation applied the run stops on

```
mshr_file.sv: entry 0 for line d1c has been live 1000 cycles,
  exceeding the liveness bound -- whatever it is waiting for is not coming
```

naming the cache, the entry and the line on the cycle the bound was crossed.
That assertion did not exist before this race was written -- see bug B18 -- and
the failure surfaced as "16 operations still outstanding after 40000 cycles",
which is the testbench giving up and says nothing about where.

**Margin, measured rather than assumed.** Under this test's deliberately
adversarial hold the worst observed MSHR age is **315 cycles against a
1000-cycle bound** and the worst TBE age **212 against 500**. The bounds are
detectors, not flake generators, and the numbers are reported by the test on
every run so that stays true.

**Mutation.** `r12-vc-alloc-ignores-eligibility` puts bug B14 back.
