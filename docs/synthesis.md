# Synthesis

Two flows, and the difference between them is the point.

`syn/scripts/synth.tcl` targets Design Compiler and a foundry kit. It is
complete, its constraints are argued block by block, and **it has never been
run** — the machine this design was built on has no Synopsys licence and no
PDK. Decision D26 explains why it was written anyway and what the three checks
that stand in for it do and do not buy.

`syn/yosys/` targets yosys, abc and OpenSTA against ASAP7 and sky130. All of
those are free, so this one runs, and the numbers below came out of it rather
than out of an estimate.

```sh
make -C syn/yosys tools          # what is installed, and where to get the rest
make -C syn/yosys pdk-asap7      # fetch and merge the Liberty
make -C syn/yosys pdk-sky130
make -C syn/yosys asap7          # every block
make -C syn/yosys sky130
make -C syn/yosys report         # the table below
```

---

## Read the caveats before the numbers

Three of them, and the third is the one that would otherwise be quoted wrongly.

**The arrays are black boxes.** `sram_1rw` is replaced by a stub with pin
directions and no timing arcs, and `syn/yosys/sta.tcl` budgets half the period
on each side of it. So the area is the **control logic's area** — the L1's
4 KB and the L2 bank's 16 KB are not in it — and the array access time is
absent. Decision D25 argues that a flop-based number would be an artefact of
the behavioural model rather than a fact about the design, and that a
hand-written Liberty would produce a timing report that looks signed off and is
not. What is timed is the logic that computes the address and the logic that
consumes the read data, which is the part this project wrote.

**There is no place and route, and no repair_design.** abc's combinational
network begins after the flops, so its `buffer` pass never sees a net driven by
a register or a primary input. In a complete flow OpenROAD's `repair_design`
inserts the buffer tree; there is no OpenROAD here. A control signal broadcast
to a few hundred sinks therefore keeps whatever single gate drives it, and the
Liberty extrapolates a large delay for it — real in kind, wrong in size.

That is why every row below carries **the worst single stage and its fanout**
beside the critical path. A path whose worst stage is a fanout-300 net is
mostly a statement about the missing buffer tree; one whose stages are all
small fanout is a statement about the design. Subtracting the former from the
critical path gives a rough floor for what the logic would do with a proper
buffer tree — rough, and stated as such.

**One corner each, and no wire load.** ASAP7 at TT (0.70 V, 25 °C) and SS
(0.63 V, 100 °C); sky130 at TT (1.80 V, 25 °C) only, which is the corner
OpenROAD-flow-scripts publishes. Interconnect is zero: pre-layout with no wire
load model, every net is a wire of no capacitance beyond its sinks' pin loads.

---

## Measured

<!-- BEGIN results -->
<!-- END results -->

---

## What went wrong building this, and why it is written down

Three flow bugs, two of which produced *confident wrong answers* rather than
failures. Both are now checks.

**The merged Liberty was missing most of its timing.** ASAP7 ships its cells
across five files and abc reads only one, so they have to be merged. The first
merger kept the first file's header and appended everyone else's cells — but
`lu_table_template`s are declared in the **header**, so most cells lost their
timing models. The result parses, loads without error, and reports that every
target period closes with exactly zero slack, because most gates have been
modelled as free. It would have claimed 5 GHz.

`merge_lib.py` now unions every named support group across all inputs and
refuses to emit a library whose timing groups reference an undeclared template.
`oss_synth.py` treats OpenSTA's `no table models found` warning as fatal rather
than as one line among thousands.

**A slack in the wrong unit.** `sta::worst_slack_cmd` returns seconds;
formatting that as nanoseconds prints `-0.0000` for every design, which reads
as "closes with zero slack". A 1.15 ns critical path was reported as meeting a
0.12 ns target. Slack is now parsed from the transcript, in the units the
transcript states.

**A `$write` in a gate-level netlist**, which is bug B24 in the bug log: five
`$error` calls in synthesizable RTL, outside the guard every assertion in this
design already uses. Lint, both front ends and elaboration with `SYNTHESIS`
defined had all passed them, because none of them had to emit anything.

The pattern across all three is worth naming, because it is the same one the
verification chapters are about: **the failure mode that matters is not the
tool refusing, it is the tool answering.** A refusal gets fixed in a minute. An
answer that is 5 GHz because half the library is missing gets quoted.

---

## What this does not do, that a real flow would

* **Place and route.** No floorplan, no clock tree, no routing, so no
  post-layout timing and no real area. OpenLane 2 would do it for sky130 and
  OpenROAD-flow-scripts for ASAP7; neither can run here — no Docker daemon and
  no Nix — which is why the flow stops at a mapped netlist.
* **Power.** No switching activity, so no meaningful dynamic power. The stress
  testbench could produce a VCD to annotate, which is real work and is not
  done.
* **Multi-corner, DFT, scan, hold fixing.** One corner per PDK, no scan
  insertion, and no hold analysis worth the name pre-CTS.
* **The memory compiler.** The largest single omission: this is a cache, and
  its arrays are not in any of these numbers.
