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
### asap7 — ss corner

`asap7_rvt_ss.lib`, arrays black-boxed, SRAM = blackbox.

| block | cells | flops | area (um^2) | critical path (ns) | f_max (MHz) | worst stage (ns) | its fanout |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tile_nic` | 13,469 | 2,753 | 1,833.2 | 1.920 | 521 | 1.045 | 302 |
| `dir_ctrl` | 104,839 | 21,124 | 14,246.5 | 3.574 | 280 | 1.840 | 264 |
| `router` | 82,244 | 17,770 | 11,282.0 | 1.899 | 527 | 0.145 | 5 |
| `dir_coh_fsm` | 60 | 0 | 5.4 | 0.300 | 3328 | 0.066 | 3 |
| `l1_coh_fsm` | 142 | 0 | 12.1 | 0.383 | 2614 | 0.086 | 15 |
| `l2_bank` | 76,117 | 15,366 | 10,388.2 | 0.846 | 1182 | 0.515 | 512 |
| `tbe_file` | 1,092 | 152 | 127.1 | 0.638 | 1566 | 0.163 | 26 |

The last two columns are the flow's limitation, not the design's: abc cannot buffer a register-driven net and there is no `repair_design` here, so a high-fanout control signal keeps whatever single gate drives it. Read the critical path as an upper bound and `critical path - worst stage` as a rough floor.

### asap7 — tt corner

`asap7_rvt_tt.lib`, arrays black-boxed, SRAM = blackbox.

| block | cells | flops | area (um^2) | critical path (ns) | f_max (MHz) | worst stage (ns) | its fanout |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tile_nic` | 13,469 | 2,753 | 1,833.1 | 1.191 | 840 | 0.696 | 302 |
| `dir_ctrl` | 104,840 | 21,124 | 14,256.2 | 2.579 | 388 | 1.497 | 264 |
| `router` | 82,244 | 17,770 | 11,282.5 | 1.257 | 795 | 0.095 | 5 |
| `dir_coh_fsm` | 60 | 0 | 5.2 | 0.245 | 4082 | 0.047 | 3 |
| `l1_coh_fsm` | 142 | 0 | 12.1 | 0.279 | 3588 | 0.056 | 15 |
| `l2_bank` | 76,118 | 15,366 | 10,389.5 | 0.539 | 1856 | 0.339 | 512 |
| `tbe_file` | 1,092 | 152 | 126.7 | 0.422 | 2367 | 0.110 | 26 |

The last two columns are the flow's limitation, not the design's: abc cannot buffer a register-driven net and there is no `repair_design` here, so a high-fanout control signal keeps whatever single gate drives it. Read the critical path as an upper bound and `critical path - worst stage` as a rough floor.

### sky130 — tt corner

`sky130_fd_sc_hd__tt_025C_1v80.lib`, arrays black-boxed, SRAM = blackbox.

| block | cells | flops | area (um^2) | critical path (ns) | f_max (MHz) | worst stage (ns) | its fanout |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tile_nic` | 11,797 | 2,753 | 127,626.2 | 7.011 | 143 | 4.384 | 302 |
| `dir_ctrl` | 103,845 | 21,124 | 1,009,837.3 | 12.358 | 81 | 6.520 | 524 |
| `router` | 59,345 | 17,770 | 797,788.9 | 8.146 | 123 | 0.546 | 1 |
| `dir_coh_fsm` | 56 | 0 | 337.8 | 2.854 | 350 | 0.697 | 4 |
| `l1_coh_fsm` | 131 | 0 | 814.5 | 3.293 | 304 | 0.654 | 1 |
| `l2_bank` | 61,615 | 15,366 | 755,534.6 | 4.459 | 224 | 2.942 | 512 |
| `tbe_file` | 905 | 152 | 8,488.1 | 3.013 | 332 | 0.437 | 11 |

The last two columns are the flow's limitation, not the design's: abc cannot buffer a register-driven net and there is no `repair_design` here, so a high-fanout control signal keeps whatever single gate drives it. Read the critical path as an upper bound and `critical path - worst stage` as a rough floor.
<!-- END results -->

---

## What did not synthesise, and the finding that came out of it

`mshr_file` and `l1_cache` are absent from the table above. Neither fits: yosys
is killed by the kernel at 10.9–14.0 GB on a 15 GB machine, at both
`--abc-effort` settings and with the hierarchy kept.

The mechanism is measured, not guessed. yosys hands abc a netlist per block,
and says how big it is:

| block | gates handed to abc | source |
| --- | ---: | ---: |
| `l1_coh_fsm` | 351 | 190 lines |
| `tbe_file` | 8,532 | 189 lines |
| `tile_nic` | 24,430 | 520 lines |
| `router` | 85,963 | 210 lines + submodules |
| `l2_bank` | 94,899 | 145 lines |
| `dir_ctrl` | 130,621 | 830 lines |
| **`mshr_file`** | **362,643** | **232 lines** |

`mshr_file` is a four-entry structure in 232 lines, and it expands to more
gates than the directory controller and the whole router combined. `tbe_file`
is the directory's equivalent — four entries, the same fully-associative CAM
shape, the same "expose every entry combinationally" interface — and it is
forty-two times smaller.

**That gap is a real finding and it is not root-caused.** What is established:
the gate count is what exhausts memory, and it is the gate count rather than
the abc script, because turning off `&dch -f` moved the ceiling from 14.0 GB to
10.9 GB and did not get under it. What is *not* established is why the RTL
expands that way. The plausible candidate is the combinational export of all
four entries — 1,490 primary outputs over 2,148 inputs, with each output cone
free to be optimised separately and nothing shared between them — but
`tbe_file` has the same interface and does not blow up, so that explanation is
incomplete.

The honest summary: **this design's L1 has not been shown to synthesise at
reasonable cost**, and the next thing to do is narrow `mshr_file` down —
probably by replacing the full-entry export with a read port, which is what a
real design would have anyway, and re-measuring. It is on the not-verified list
in `docs/verification.md` rather than glossed.

Two things this does *not* mean. It is not a correctness problem: every tier
passes, and `mshr_file` is exercised by all of them. And it is not a property
of the protocol: `l1_coh_fsm`, the thirteen-state table itself, is 142 gates
and 12 µm².

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
