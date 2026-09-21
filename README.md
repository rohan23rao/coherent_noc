# Directory MESI coherence over a 2x2 mesh NoC

Four tiles, each with a private L1, a slice of a shared inclusive L2 that
doubles as the directory for the address range it homes, its own memory
controller, and a router. Directory MESI with thirteen cache states and five
directory states, over a wormhole mesh with virtual channels and credit flow
control. Synthesizable SystemVerilog, verified with Verilator and cocotb.

```
make lint     # Verilator -Wall (design and per-module), slang, structural checks
make test     # every tier, including 100k-request stress -- about 35 minutes
make test TEST=races      # one module
make mutate   # break each protocol arc, require its test to notice
make all      # lint + test, and what a clean clone must pass
make waves TEST=<name>
make diagrams # regenerate docs/img and the state machines
make -C syn router PDK=saed32   # Design Compiler, if you have one -- see syn/README.md
```

## The design in three pictures

Four tiles on a 2x2 mesh. Each is identical: a private L1, the directory bank
that is home for a quarter of the address space, its own memory, and a router.

![2x2 mesh topology](docs/img/topology.svg)

Inside a tile, two independent protocol agents share one router port. Three
structurally separate virtual networks, separate packetisers and separate
credit pools — the deadlock argument is a piece of hardware, not a paragraph.

![tile microarchitecture](docs/img/tile_uarch.svg)

And the argument itself: a request may cause a forward, a forward may cause a
response, a response causes nothing. Three levels of dependency, three virtual
networks, and a last level that is a true sink by construction.

![virtual networks](docs/img/vnets.svg)

**`docs/diagrams.md`** has the rest — the L1 pipeline, the three writers of a
line's state, the directory, the router, the address decode, the flit format
and both state machines. The state machines are generated from the same table
the RTL is checked against, so one that is wrong could not have been produced.

## Where to start

**`docs/READING_ORDER.md`** -- the files in the order they make sense, with one
line each on what to look for.

If you have ten minutes instead: `rtl/pkg/coh_pkg.sv` for the shape,
`rtl/l1/l1_coh_fsm.sv` for the protocol, `docs/races.md` for what the protocol
is defending against, and bug **B20** in `docs/bug_log.md` for what it is
actually like to get this right.

## The docs

| File | What it is |
| --- | --- |
| `docs/spec.md` | the build specification, annotated wherever the implementation deviated |
| `docs/decisions.md` | every non-obvious choice as decision, alternatives, why, cost |
| `docs/races.md` | twelve races, each with its interleaving, its arc, its test, and the result of deleting that arc |
| `docs/bug_log.md` | twenty-four bugs: symptom, how it was localised, root cause, fix, the test that catches it now |
| `docs/deadlock.md` | the message dependency graph, why three virtual networks suffice, and the separate routing argument |
| `docs/verification.md` | the six tiers, the coverage report, and an honest list of what is not verified |
| `docs/noc_perf.md` | load-latency curves, the knee, and what the ordering rule cost |
| `docs/diagrams.md` | every figure, each stating an argument rather than labelling boxes |
| `syn/README.md` | the Design Compiler flow, what it deliberately does not do, and what to send back |
| `docs/interview_notes.md` | ten hard questions with answers |

## Current state

Every phase gate passes:

- 100,000 constrained-random requests across each of three footprints, plus an
  L2-capacity-pressure run and a same-line racing run, with no assertion
  failure, no SWMR violation and no value mismatch.
- All twelve races have a directed test that asserts the interleaving happened
  before it asserts the outcome, and fifteen mutations -- one per handling arc,
  plus three for invariants the stress tier found -- are each killed by their
  named test.
- Functional coverage has an empty list of uncovered *reachable* bins. The
  bins that are unreachable are listed with a written argument each rather
  than excluded.
- The design elaborates clean with `SYNTHESIS` defined, and again with the
  synthesis black boxes substituted -- both are part of `make lint`. The
  Design Compiler flow in `syn/` is written and argued but has **not** been
  run; `syn/README.md` says so in its first paragraph and says what to send
  back from a machine that can.

## Requirements

Verilator 5.036 or newer (5.040 here), cocotb 2.x, Python 3.11. slang is
optional -- `make lint` runs it as a second opinion if it is installed and says
so if it is not.
