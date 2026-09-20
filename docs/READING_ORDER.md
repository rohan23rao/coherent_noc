# Reading order

Twenty-odd files, and the order matters. This is the order I would read them in
to understand the design, with one line each on what to look for.

Read the first four before anything else; after that the two halves --
coherence and network -- are independent and can be read in either order.

## Start here

| # | File | What to look for |
| --- | --- | --- |
| 1 | `rtl/pkg/coh_pkg.sv` | Every parameter, state and message in one place. Two things to notice: the L1 tag is **non-contiguous**, because the home-bank bits sit below the index field; and `ack_cnt` is declared **signed**, which is load-bearing. |
| 2 | `rtl/l1/l1_coh_fsm.sv` | The cache's thirteen states as a pure function. Read it as a table: rows are states, columns are events, and a blank cell raises `illegal` rather than doing something plausible. |
| 3 | `rtl/l2/dir_coh_fsm.sv` | The directory's five states, same shape. `S_D` is the one to understand -- "I have asked the owner for the data and cannot serve anyone until it arrives". |
| 4 | `docs/races.md` | Twelve interleavings with sequence diagrams. This is what the two tables above are actually for, and it is the fastest way to understand why the transients exist. |

## The coherence half

| # | File | What to look for |
| --- | --- | --- |
| 5 | `rtl/l1/mshr_file.sv` | A small CAM. Note the signed arithmetic on `ack_cnt` and the liveness assertion, which is the cache-side deadlock detector. |
| 6 | `rtl/l1/l1_cache.sv` | The biggest file, and the one with the most subtlety. Read the header's three numbered notes first. Then look at `s1_coh_conflict`: three stages write a line's state and only one may do it per cycle (D23). Then the replay path, and why a request that triggers an eviction still has to replay. |
| 7 | `rtl/l2/tbe_file.sv` | The directory's transaction buffer, and the other liveness bound. The age counter lives inside `` `ifndef SYNTHESIS `` because it exists only for the assertion. |
| 8 | `rtl/l2/l2_bank.sv` | Storage. The point is that directory metadata lives *beside* the L2 tag -- one structure, not two -- which is what makes the L2 inclusive by construction. |
| 9 | `rtl/l2/dir_ctrl.sv` | The directory controller. Follow one request through `D_LOOK -> D_EXEC -> D_SEND`, then follow a back-invalidation through `D_BINV -> D_BSEND -> D_BWB -> D_BFREE`. Watch `data_valid`: it says whether the L2's copy is the current one, and bug B21 was that it was never restored. |

## The network half

| # | File | What to look for |
| --- | --- | --- |
| 10 | `rtl/noc/route_compute.sv` | Four lines of XY routing, and the deadlock argument that comes with it. |
| 11 | `rtl/noc/input_unit.sv` | Per-VC buffers and the VC state machine. A VC holds one packet at a time; that fact explains the buffer depth. |
| 12 | `rtl/noc/vc_allocator.sv` | Two things, both bug fixes. Eligibility is computed *before* stage one, or a blocked VC starves everything behind it (B14). And the output VC index equals the input's, which is where point-to-point ordering comes from (B19, D22). |
| 13 | `rtl/noc/switch_allocator.sv`, `crossbar.sv`, `router.sv` | Conventional. Read the router's header for the pipeline and skim the rest. |
| 14 | `rtl/tile/tile_nic.sv` | Packetisation and reassembly. The important structure is that the three virtual networks are **physically separate** all the way through -- separate queues, separate ejection paths, separate credits. Three different bugs came from sharing one of those. |
| 15 | `rtl/tile/tile_top.sv`, `rtl/top/system_top.sv` | Wiring. Note the single reset synchroniser, and the `msg_hold` elements, which are where the race tests inject their delays. |

## The verification

| # | File | What to look for |
| --- | --- | --- |
| 16 | `docs/verification.md` | The six tiers, and -- more usefully -- the list of what is *not* verified. |
| 17 | `tb/models/tables.py` | The two protocol tables, transcribed independently of the RTL. The table tests compare the two; the coverage model uses the keys as its legal-bin list. |
| 18 | `tb/models/probe.py` | How a race test proves the race happened. Note that it records events that are *presented*, not accepted -- stall cells are never accepted and they are half the catalogue. |
| 19 | `tb/tests/test_races.py` | The twelve directed tests. Each one forces, witnesses, then checks. |
| 20 | `tb/models/coverage.py` | The bins, and the written argument for every legal cell the runs cannot reach. That list is a set of claims about the implementation, not a set of excuses. |
| 21 | `scripts/mutations.py` | Twelve ways to break the design on purpose, each naming the test that must notice. This is the evidence that the tests above have teeth. |

## The arguments

Read these when you want to know *why* rather than *what*:

- `docs/decisions.md` -- every non-obvious choice as decision, alternatives,
  why, and cost. Twenty-four entries.
- `docs/bug_log.md` -- twenty-one bugs, each with how it was localised and the
  test that catches it now. The last six are the interesting ones; they are
  all races or ordering, and none of them were found by directed testing.
- `docs/deadlock.md` -- the message dependency graph and why three virtual
  networks suffice.
- `docs/noc_perf.md` -- load-latency curves and where the knee is.
- `docs/interview_notes.md` -- ten hard questions with answers.

## If you only have ten minutes

`coh_pkg.sv` for the shape, `l1_coh_fsm.sv` for the protocol, `docs/races.md`
for what the protocol is defending against, and bug **B20** in the bug log for
what it is actually like to get this right.
