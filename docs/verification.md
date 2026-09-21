# Verification

What is checked, by what, and what is not checked at all.

The short version: six tiers, each of which can fail for a different reason,
and a mutation suite whose job is to prove the tiers have teeth. The honest
list at the bottom is the important part of this document -- a verification
plan that does not say what it leaves out is a marketing document.

---

## The tiers

### 1. Unit

`tb/tests/test_unit_*.py`. One module each: the round-robin arbiter, the FIFO,
the skid buffer, the credit counter, the SRAM. Randomised stimulus against a
Python model of the module's contract.

**Proves:** each building block does what its header says. Notably the
arbiter's fairness property -- the pointer advances only when a grant is
*taken* -- which is measured over 10,000 cycles rather than asserted.

**Cannot prove:** anything about how they are composed.

### 2. Network standalone

`tb/tests/test_noc_router.py`, `test_noc_mesh.py`. A single router with all
5x5 turns exercised, then the 2x2 mesh with traffic generators: uniform random,
tornado, and an all-to-one hot spot.

**Proves:** every injected packet is ejected exactly once, at the right node,
with its flits in order and its virtual network unchanged. Credit and buffer
assertions are live throughout. The load-latency curve and its knee are in
`docs/noc_perf.md`.

**Cannot prove:** anything about message *content* or about the protocol. A
network that delivers every packet correctly can still reorder two of them --
which is exactly what bug B19 was, and this tier passed throughout.

### 3. Protocol tables

`test_protocol_l1_table.py`, `test_protocol_dir_table.py`. Every (state, event)
pair of both tables, compared cell by cell against an independent Python
transcription in `tb/models/tables.py`, in MSI and MESI modes.

**Proves:** the RTL tables match the specification's tables, including that
every blank cell raises `illegal` -- there is no permissive default anywhere.
Running the directory table in both modes also pins the claim that enabling E
changes exactly one arc.

**Cannot prove:** that the table is the right table, or that the controller
around it drives the table correctly. Two transcriptions of the same mistake
would agree.

### 4. Directed protocol

`test_protocol_msi.py`, `test_protocol_mesi.py`, `test_protocol_noc.py`,
`test_protocol_inclusion.py`, and the twelve races in `test_races.py`. Each
scenario runs first on a direct-connect harness and then over the mesh, from
the same body, so that a failure that appears only in the second is a network
failure and nothing else.

**Proves:** the named interleavings happen and are handled. Each race test
asserts a **witness** -- the exact table cell it is named after was presented
to the controller -- before it asserts anything about values, because a test
that checks only outcomes cannot distinguish "handled correctly" from "never
happened". Race R5 had passed for three phases while never producing the
message in its name; see bug B17.

**Cannot prove:** that the catalogue is complete. It is a list of the races
somebody thought of.

### 5. Constrained random

`test_stress.py`. Five configurations in one simulation:

| Configuration | Footprint | What it is for |
| --- | --- | --- |
| 4 lines | one L1 set, two ways | thrashes on every access: evictions and upgrades continuously |
| 16 lines | four sets | the general case |
| 256 lines | sixty-four sets | miss and fill paths rather than conflict |
| L2 pressure | 48 lines, all in one L2 set per bank | more lines per set than the L2 has ways, so back-invalidation runs continuously |
| racing | 4 lines, same-line concurrency allowed | the only configuration that reaches the transient-state forward arcs |

The first four hold one operation per line at a time, which is what makes the
golden atomic memory's answer well defined, and every response is checked
against it. The racing configuration lifts that, so no individual response has
a predictable value; there the checks are the SWMR monitor, every assertion in
the design, and a plausibility check that a load returns a value somebody
stored.

Per-virtual-network delays are re-randomised throughout, with long holds on
requests -- the stale-Put races need a request delayed past two changes of a
line's directory state -- and medium holds on forwards and responses, which
widen the window a directory spends in `S_D`.

**Proves:** the design survives 100,000 requests per footprint with no
assertion failure, no SWMR violation and no value mismatch. It found five of
the project's worst bugs: B16, B19, B20 (three separate manifestations) and
B21.

**Cannot prove:** absence. It is random, and the parts of the state space it
does not reach are exactly the parts the coverage report is for.

### 6. Mutation

`make mutate`. Twelve single-line RTL mutations, each deleting one protocol arc
and naming the test that must then fail.

**Proves:** the directed tier is sensitive to the arcs it claims to cover. A
mutation that survives is an open item, not a pass.

**Cannot prove:** that the arcs not in the table are unnecessary.

---

## Assertions

The assertions are part of the design, under `` `ifndef SYNTHESIS ``, not part
of the testbench. They are what makes every tier above able to fail for the
right reason.

- Illegal transition: `$error` on any (state, event) pair either table leaves
  blank, at both the cache and the directory.
- `ack_cnt` is credited onto a count that is at or below zero -- catching a
  Data message delivered twice.
- MSHR and TBE address uniqueness; no allocation over a live entry; no free of
  an entry that was not allocated.
- No L2 eviction of a line with a live TBE, and always a legal victim.
- The L2's copy is current whenever the directory records a line as I or S
  (`a_l2_copy_current`) -- the invariant bug B21 violated.
- Credit bounds, no send on zero credits, no VC buffer overflow, no packet
  changing virtual network, and no packet changing **virtual channel**
  (`a_same_vc`) -- the ordering property bug B19 was about.
- Every VN2 response lands on an MSHR; the VN2 input queues never fill.
- **Liveness:** every MSHR retires within `MSHR_TIMEOUT` and every TBE within
  `TBE_TIMEOUT`. These are the deadlock detector, and they are real assertions
  inside the design rather than a testbench watchdog -- see decision D20 for
  the difference that made, measured.

Both bounds are parameters, because raising one and re-running is how a
suspected deadlock is told apart from a slow path. Both are reported against
their measured worst case on every stress run rather than assumed adequate.

---

## The instruments

Three of the bugs in Phase 11 were found by a liveness assertion saying that
something was stuck, and a liveness assertion does not say *what it is waiting
for*. These exist for that gap, and they are switched on by an environment
variable rather than always running:

| Instrument | Switch | What it gives |
| --- | --- | --- |
| `tb/models/probe.py` | always, in directed tests | every (state, event) pair presented to either table, as it happens |
| `tb/models/probe.py::LivenessGauge` | always, in stress runs | the worst MSHR and TBE age observed, against the bounds |
| `tb/models/hangdump.py::stuck/dump` | `STRESS_HANG=<cycles>` | the whole machine the moment any entry has been live too long: every directory's FSM and TBEs, every cache's MSHRs and what its inputs are offering, every network interface's reassembly slots with the *messages* in them, and every router's VC states and buffer occupancy |
| `tb/models/hangdump.py::Tracer` | `STRESS_HANG` | a ring buffer of the last few hundred messages, printed with the dump |
| `COCOTB_CASE=<name>` | environment | run one coroutine out of a module, for iterating on a single failure |

The dump plus the ring buffer is what turned "an MSHR has been live 3,000
cycles" into a readable sequence -- a cache with every acknowledgement it was
waiting for, still in `SM_AD`, stalling a forward that two directories were
ultimately blocked behind. That is bug B20's third manifestation, and nothing
short of the history would have identified it.

They read internal signals by name, which is exactly what `docs/decisions.md`
argues against for the SWMR checker. The difference is audience: the checker is
a correctness monitor that has to survive elaboration, while these are
debugging instruments for a handful of tests, and pinning them to signal names
is acceptable where those tests are rewritten whenever the controller is.

---

## Coverage

Functional coverage is collected across all five stress configurations and
reported at the end of the run. The bins are the ones the specification asks
for: the cross of (L1 state x event), the cross of (directory state x event),
MSHR occupancy, VC occupancy, sharer count, and one bin per named race.

The legal-bin list for the two crosses is the key set of
`tb/models/tables.py` -- the same transcription the table tests check the RTL
against, so "legal" means one thing in this project.

**The uncovered list is not empty, and that is the honest outcome.** It is
split in two:

- **Uncovered and reachable** -- an open item. This list is empty.
- **Uncovered by construction** -- a cell the protocol table defines but the
  microarchitecture forecloses earlier. Twenty cache cells, six directory
  cells, and one occupancy bin, each with a written argument in
  `tb/models/coverage.py` and each printed in the report rather than silently
  excluded. The arguments fall into six groups:

  | Group | Argument |
  | --- | --- |
  | Inv in IM_AD / IM_A | a cache in those states is not in any sharer vector |
  | Evict in a fetch transient | the way is reserved by its own MSHR and never offered as a victim |
  | Core events in MI_A / EI_A / SI_A / II_A | an evicting line's state lives in its MSHR; the core pipeline sees I and replays |
  | Data-owner in SM_AD | a directory serving a GetM from S has no owner to forward to |
  | PutS(not last) in I, E, M | the sharer vector is empty there, so `is_last_sharer` is always true |
  | Put(owner) outside E and M | `is_owner` tests the directory state, so the owner variants cannot be generated elsewhere |
  | VC occupancy = 4 | a VC holds one packet and the longest packet is three flits |

A cell in that table is a claim about the implementation. If one of them is
wrong, the right outcome is that a future test reaches the cell and the report
shrinks -- not that the claim quietly becomes true.

---

## What is NOT verified

This is the part to read.

**The memory consistency model.** Not even slightly. The golden model is an
atomic, sequentially-consistent memory and the driver issues one operation per
core at a time with no fences, no store buffer and no reordering, so the
testbench cannot distinguish SC from TSO from anything weaker. What is checked
is **coherence** -- a single total order per line -- which has a simple
sequential specification and is a strictly weaker property. A design that
passed every test here could still have a consistency model nobody would want.

**Multi-word atomics.** There are no atomic read-modify-write operations in the
core interface, so nothing exercises the load-linked/store-conditional or
compare-and-swap paths a real cache would need, and no reservation state
exists to get wrong.

**Byte-enable interactions under racing.** The racing configuration issues
full-word stores only, because a byte-enabled store's result depends on what
the line held first, which is precisely what is undefined when two cores race.
Byte enables are exercised in the checked configurations, where the order is
known.

**ECC, parity, and any other error handling.** The arrays are plain flops and
SRAM with no protection and no poison, and nothing models a fault.

**Power, clock gating, and any physical property.** No power intent, no
multi-corner timing, no synthesis. "Synthesizable-shaped" is a coding
discipline here, not a result: the design has never been through a synthesis
tool, so its frequency, area and gate count are unknown.

**Scaling.** Everything is verified at four tiles on a 2x2 mesh with a
four-entry MSHR file and a four-entry TBE file. The full sharer vector, the
2-bit owner field and the XY router all have obvious limits beyond that, and
none of them have been tested near those limits.

**The memory model's aliasing.** `mem_model` indexes with the low bits of the
line address, so addresses further apart than `MEM_LINES` alias. An assertion
flags any access outside the modelled window, so the aliasing cannot be silent,
but the window is a testbench constraint that a real system would not have.

**Reset behaviour beyond the single synchronizer.** Reset is synchronised once
and fanned out, and every test starts from it, but no test asserts reset
mid-transaction or checks that the machine recovers.
