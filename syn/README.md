# Synthesis

A Design Compiler flow for this design, PDK-agnostic, driven from one Makefile.

**Read this first: nothing in this directory has been run.** The machine this
repository was developed on has no Synopsys tools and no PDK. The RTL is
checked under `SYNTHESIS` here — see [What *has* been
checked](#what-has-been-checked) — but the Tcl has never been executed by
`dc_shell`. Expect to fix something on the first run. The two most likely
things are a library path that does not match your kit's layout
(`syn/setup/pdk.tcl`, and it is the only file that should need editing) and a
cell name for `set_driving_cell` that your library spells differently.

---

## Running it

```sh
ssh <your-cad-machine>
cd <wherever you cloned this>

export SAED32_ROOT=/path/to/SAED32_EDK        # or ASAP7_ROOT, or SKY130_ROOT
module load synopsys/dc                        # whatever your site uses

make -C syn router                             # start here: smallest block
make -C syn all                                # every block, smallest first
```

Options:

| Variable | Values | Default | What it does |
| --- | --- | --- | --- |
| `PDK` | `saed32` `asap7` `sky130` | `saed32` | which library to map to |
| `PERIOD` | a number | per PDK | clock period in library time units |
| `SRAM` | `blackbox` `flops` | `blackbox` | see [The arrays](#the-arrays) |
| `EFFORT` | `ultra` `high` | `ultra` | `high` drops to plain `compile`, no DC Ultra licence needed |
| `DC` | a path | `dc_shell` | if `dc_shell` is not on `PATH` |

```sh
make -C syn router    PDK=asap7  PERIOD=0.6
make -C syn l1_cache  PDK=saed32 PERIOD=2.5 EFFORT=high
make -C syn tile_top  PDK=sky130 PERIOD=12
```

Everything lands in `syn/out/<block>.<pdk>/`.

### sky130 needs one extra step

Design Compiler reads `.db`, and sky130 ships ASCII `.lib`. Compile it once:

```sh
lc_shell -x "read_lib sky130_fd_sc_hd__ss_100C_1v60.lib; \
             write_lib sky130_fd_sc_hd__ss_100C_1v60 \
               -format db -output sky130_fd_sc_hd__ss_100C_1v60.db"
```

and point `SKY130_ROOT` at the directory holding the result.

---

## Which blocks, and in what order

Smallest first, because the first run is about making the flow work, not about
the number.

| Block | Why it is worth synthesising |
| --- | --- |
| `router` | Pure logic, no arrays. The critical path is the allocator loop — VA feeding SA feeding the crossbar select — and it is the one path in this design that a textbook would predict. Start here. |
| `tile_nic` | Packetisation and three independent credit paths. Small, and the first block where the three-virtual-network separation costs real area. |
| `l1_cache` | The interesting one. Three writers of a line's state, a 4-entry CAM, and a combinational `core_req_ready_o`. |
| `dir_ctrl` | The 12-state controller plus the recall path. Compare its critical path with `l1_cache`'s: they solve similar problems at different scales. |
| `tile_top` | The first level where L1 → `msg_hold` → NIC → router is a real path rather than an assumption held in two separate SDC files. |
| `system_top` | Slow, and the area is whatever `SRAM` is set to. Worth doing once, to confirm the four tiles' critical paths are the same path. |

---

## The arrays

`SRAM=blackbox` (the default) replaces `rtl/lib/sram_1rw.sv` with an empty
module of the same name and ports, so DC leaves a black box in the netlist.
`syn/constraints/common.sdc` budgets half the clock period on each side of it,
so the logic that computes the address and the logic that consumes the read
data are both still timed.

What that run does **not** tell you is the array access time. That comes from
the foundry memory compiler's `.lib`, and this design has no memory compiler
behind it. So:

* the number to quote from a black-box run is the **control-logic critical
  path**;
* the area is the **control logic's area**, and the arrays have to be added
  from a memory compiler datasheet;
* a report that shows a beautiful 3 GHz `l1_cache` is not lying, it is
  answering a smaller question than it looks like.

`SRAM=flops` synthesises the behavioural array into flip-flops instead. That is
about 170 kbit per tile and 680 kbit for the system, which is not a cache
anybody would build — but it is worth running once on `l1_cache` to see the tag
array's real cost, because 64 × 2 × 21 bits genuinely *is* small enough to be
flops in a real design.

`mem_model` is always a black box. It is a testbench component standing in for
a memory controller, and it is not part of the design.

---

## What has been checked

The Tcl has not run. These have, and they are in `make lint`, so they run on
every commit:

* **`make lint-synth`** — Verilator elaborates the whole design with
  `SYNTHESIS` defined, which is exactly the source DC reads: every SVA block
  and every debug `$display` compiled out. Four signals exist only to feed an
  assertion, and `lint/waivers_synth.vlt` names them and names the assertion
  that reads each one. Anything else this pass reports is real.
* **`make lint-synth-bb`** — the same elaboration again, with
  `syn/blackbox/*.sv` substituted. This is the check that stops a stub's port
  list drifting from the module it stands in for: a renamed port is a
  `PINNOTFOUND` error here, and no waiver in `lint/waivers_blackbox.vlt`
  suppresses it. Without this check that drift surfaces as a `dc_shell` error
  two minutes into a run on a machine this repository cannot log into.
* **`scripts/check_style.sh`** — no `initial`, no `#delay`, no `force`/
  `release` anywhere under `rtl/`; every `always_ff` uses the same
  asynchronous reset or is explicitly annotated as unreset.
* **The file list is not duplicated.** `syn/Makefile` pulls it from
  `sim/Makefile` on every run, so what is synthesised is by construction what
  is simulated.

---

## What to send back

From `syn/out/<block>.<pdk>/`, in this order — and the first two matter more
than the third:

1. **`check_timing.rpt`** — before anything else. An unconstrained path is
   worse than a violating one, because a violating path is in the report and an
   unconstrained path is silent. If this lists unclocked registers or ports
   with no input delay, the timing report below is meaningless and the SDC is
   what needs fixing.
2. **`check_design.rpt`** — run before `compile`, so it is about the RTL rather
   than the netlist. Unconnected ports, inferred latches, multiply-driven nets.
   A latch in this design would be a bug; there should be none.
3. **`qor.rpt`** — one screen: worst slack, total negative slack, cell count,
   area.
4. **`timing.rpt`** — the critical path with the cells on it. This is the one
   worth arguing about.
5. **`area.rpt`** — where the area went, by hierarchy.
6. **`synth.log`** — if anything failed, this is the whole story.

Sending just `qor.rpt` is the common mistake: a slack number with no
`check_timing` behind it is a number with no argument behind it.

### The questions worth asking of the results

* Is the `router` critical path the VA → SA → crossbar-select loop? If it is
  something else, either the tool found something the design did not intend or
  the SDC is wrong.
* Is `l1_cache`'s critical path through `core_req_ready_o`? It is
  combinational from `array_free`, which is deliberate and is called out in
  `syn/constraints/l1_cache.sdc` — a real integration would pipeline that
  interface, and this is where the cost of not doing so becomes a number.
* Does `dir_ctrl` land near `l1_cache`? They solve similar problems — a small
  CAM, a table lookup, a wide state update — so a large gap between them is
  worth explaining.
* At `tile_top`, does the critical path cross a module boundary? If it does,
  the `msg_hold` register that decision D16 put on the NIC boundary is not
  doing its job, and that is a real finding.

---

## What this flow deliberately does not do

* **No place and route.** IC Compiler would give a post-CTS number that is
  worth more than a pre-CTS one, and it is the obvious next step. It is not
  here because the pre-CTS answer is the one that changes the RTL, and the RTL
  is what this project is about.
* **No power sign-off.** `report_power -analysis_effort low` runs and is
  written out, but without switching activity from a real workload it is a
  structural estimate. The stress testbench could produce a VCD to annotate,
  which would make it mean something; that is a real piece of work and it is
  not done.
* **No multi-corner.** One slow corner, which is what a QoR conversation needs.
  Sign-off needs three or more.
* **No DFT, no scan, no clock gating insertion beyond what `compile_ultra`
  infers.** `report_clock_gating` is written out so you can see what it found.
