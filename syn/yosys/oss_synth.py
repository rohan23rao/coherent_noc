#!/usr/bin/env python3
"""Open-source synthesis: sv2v -> yosys -> abc, against a real standard-cell library.

This flow RUNS, which is the whole reason it exists beside syn/scripts/synth.tcl.
The Design Compiler flow in this repository has never been executed -- there is
no Synopsys licence on the machine that built this design -- so every number it
could have produced is absent. yosys, ASAP7 and sky130 are all free, so the
numbers below are measured rather than predicted.

What it produces, per block and per corner:

  * cell count and chip area, from `stat -liberty` -- real areas out of the
    vendor Liberty, in um^2
  * flop count, which is the number that explains most of the area
  * a critical-path delay, from abc's `stime` on the mapped netlist

and what that delay is NOT, stated here so it is not quoted as something it is
not: abc's `stime` walks the mapped netlist using the Liberty's NLDM tables,
with ZERO interconnect. No wire capacitance, no wire load model, no clock tree,
no uncertainty, no SDC. It is a lower bound on the gate delay of the path abc
believes is critical. A real number needs a real STA against an SDC, which is
what `syn/yosys/sta.tcl` does when OpenSTA is available.

Usage:
  oss_synth.py --top router --pdk asap7 --corner tt --period 500
  oss_synth.py --top l1_cache --pdk sky130 --corner tt --period 10000
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".work")

# Elaboration parameters, the same ones syn/scripts/synth.tcl uses, so the two
# flows synthesise the same thing.
# Only values that DIFFER from the module's own default are overridden.
#
# router's MY_X/MY_Y, tile_nic's and tile_top's TILE_ID and dir_ctrl's BANK_ID
# all default to 0, which is the corner instance and the pessimistic choice
# anyway -- at MY_X=1 on a 2x2 mesh route_compute's edge comparisons fold away.
# Passing them again through `-chparam` makes yosys derive a parameterised copy
# of a module that is also instantiated elsewhere in the file, and yosys 0.33
# then fails an internal assertion instead of reporting an error. So the list
# is kept to what actually needs saying.
#
# dir_ctrl is the one that does: its ENABLE_E defaults to 0 (MSI) while the
# system instantiates it at 1 (MESI), and MESI is what this design ships.
TOP_PARAMS = {
    "dir_ctrl": {"ENABLE_E": 1},
}

# Modules replaced by an empty stub and marked as black boxes. Same
# substitution as the DC flow, and decision D25 argues it: a behavioural array
# maps to flip-flops, which answers a smaller question than it looks like.
BLACKBOX = ("sram_1rw", "mem_model")

# Where each PDK's Liberty lives, what cell drives the inputs during STA, and a
# sensible sweep bracket. The brackets are an order-of-magnitude guess to start
# the search, not a claim -- the sweep reports where it actually landed, and
# refuses to answer if the answer is outside the bracket.
PDKS = {
    "asap7": {
        "lib": "/opt/pdk/asap7/merged/asap7_rvt_{corner}.lib",
        "driver": "INVx2_ASAP7_75t_R",
        "sweep": (40.0, 1200.0),
        "corners": {"tt": "0.70 V, 25 C", "ss": "0.63 V, 100 C"},
    },
    "sky130": {
        "lib": "/opt/pdk/sky130/merged/sky130_fd_sc_hd__{corner}_*.lib",
        "driver": "sky130_fd_sc_hd__inv_2",
        "sweep": (1500.0, 30000.0),
        "corners": {"tt": "1.80 V, 25 C"},
    },
}


def run(cmd, log=None, **kw):
    if log:
        with open(log, "w") as f:
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, **kw)
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def rtl_sources(sram):
    """The file list, from sim/Makefile, with the black boxes substituted."""
    r = run(["make", "-C", os.path.join(ROOT, "sim"), "print-rtl-srcs"])
    files = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("/")]
    if not files:
        raise SystemExit("could not read the file list from sim/Makefile")
    out = []
    for f in files:
        base = os.path.basename(f)
        if base == "mem_model.sv" or (base == "sram_1rw.sv" and sram == "blackbox"):
            out.append(os.path.join(ROOT, "syn", "blackbox", base))
        else:
            out.append(f)
    return out


def sv2v_convert(files):
    """SystemVerilog -> Verilog-2005, cached on the inputs.

    yosys's own SystemVerilog front end does not take this design: packages,
    packed structs and typed enums are all beyond it. sv2v is the usual answer
    and is what the open-source flows use.
    """
    os.makedirs(WORK, exist_ok=True)
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode())
        h.update(open(f, "rb").read())
    key = h.hexdigest()[:16]
    dst = os.path.join(WORK, f"design_{key}.v")
    if not os.path.exists(dst):
        sv2v = shutil.which("sv2v")
        if not sv2v:
            raise SystemExit(
                "sv2v is not on PATH. Releases: github.com/zachjs/sv2v")
        r = run([sv2v, "-DSYNTHESIS", f"--write={dst}"] + files)
        if r.returncode != 0:
            sys.stderr.write(r.stdout + r.stderr)
            raise SystemExit("sv2v failed")
    return dst


def yosys_script(design, top, lib, period_ps, outdir, max_fanout=16,
                 flatten=True):
    # `chparam -set` rewrites the module's own defaults. Passing the same
    # values through `hierarchy -chparam` makes yosys derive a $paramod copy,
    # which then collides with the plain module when `synth -top` re-resolves
    # the hierarchy -- an assertion failure inside yosys 0.33 rather than an
    # error message, which took a while to recognise.
    params = "".join(f" -chparam {k} {v}"
                     for k, v in TOP_PARAMS.get(top, {}).items())
    bb = " ".join(BLACKBOX)
    flatten = " -flatten" if flatten else ""
    return f"""
read_verilog {design}
# The arrays and the behavioural memory are black boxes; see decision D25.
setattr -mod -set blackbox 1 {bb}
hierarchy -check -top {top}{params}
# `check -assert` before synthesis: a combinational loop or a multiply-driven
# net is a design error, and finding it here names the signal.
proc; opt_expr; opt_clean
check -assert
# `synth` runs its own `hierarchy`, and giving it `-top {top}` makes it look
# for a module that `-chparam` above may have replaced with a derived copy --
# yosys 0.33 answers that with an internal assertion rather than an error. So
# synth operates on the top hierarchy already selected, and the derived name is
# renamed back afterwards so OpenSTA can link the design by the name the
# designer uses.
# Flattened by default, and the measurement says why.
#
# Run hierarchically, the router's critical path is 3.68 ns with a fanout-290
# stage in it; flattened it is 1.26 ns with a fanout-5 stage. abc optimises one
# module at a time and cannot buffer or restructure across a boundary, so
# hierarchy does not merely cost provenance in the report -- it costs a factor
# of three in the answer, and the high-fanout nets that dominate these paths
# are exactly the ones that cross boundaries.
#
# The cost is memory: flattening l1_cache put yosys at 12.8 GB and the kernel
# killed it. That block is run with --no-flatten, and its row says so, because
# a number produced a different way does not belong in the same column without
# a label.
synth{flatten}
# Flop count before technology mapping, where the cells are generic $_DFF_*
# and the count does not depend on how a particular library spells its
# registers. Counting mapped cells by name worked for ASAP7 and silently
# reported zero flops for sky130.
#
# Restricted to the top module: `stat` with no selection prints a block per
# module AND a design-wide summary, so summing the whole transcript counts
# every flop at least twice. Flattened, the top module is the design.
tee -o {outdir}/stat_premap.txt stat -top {top}
dfflibmap -liberty {lib}
# yosys's default `abc -D` maps for delay but leaves the netlist UNBUFFERED:
# a net with a thousand sinks keeps whatever gate happened to drive it, and the
# Liberty's NLDM tables then extrapolate far past their characterised load. The
# first run of this flow reported a NOR2 with 2.8 ns of delay at 7nm, which is
# not a design fact, it is an unbuffered net.
#
# So the abc script is spelled out, with `buffer` to cap fanout and
# `upsize`/`dnsize` to pick drive strengths -- the same tail the OpenROAD flow
# scripts use. Without it the timing reports are fiction.
abc -liberty {lib} -D {period_ps} -script +strash;&get,-n;&dch,-f;&nf,-D,{period_ps};&put;buffer,-N,{max_fanout};upsize,-D,{period_ps};dnsize,-D,{period_ps}
setundef -zero
opt_clean -purge
# A $display or $error left outside `ifndef SYNTHESIS survives into the
# netlist as a $print cell, which yosys then emits as a $write statement and
# the downstream Verilog reader rejects. That happened (bug B24). Deleting them
# here would hide it, so the flow asserts they are absent instead -- and
# scripts/check_style.sh catches it at lint, long before this point.
select -assert-none t:$print
rename -top {top}
write_verilog -noattr {outdir}/netlist.v
write_blif {outdir}/mapped.blif
stat -liberty {lib}
"""


def blackbox_liberty(netlist, outdir):
    """Rename black-box instances and emit a pin-only Liberty for them.

    Returns the Liberty path, or None if the netlist has no black boxes --
    router and tile_nic contain no arrays, so they take that path.
    """
    import subprocess as sp
    lib = os.path.join(outdir, "blackboxes.lib")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "blackbox_lib.py")
    r = sp.run([sys.executable, script, netlist, lib],
               capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit("blackbox_lib.py failed")
    return lib if os.path.exists(lib) else None


def opensta(top, lib, netlist, period_ns, outdir, driver=None, bb_lib=None):
    """Real static timing, if OpenSTA is installed. See syn/yosys/sta.tcl."""
    sta = shutil.which("sta")
    if not sta:
        return None
    env = dict(os.environ,
               STA_LIB=lib, STA_NETLIST=netlist, STA_TOP=top,
               STA_PERIOD_NS=str(period_ns), STA_OUT=outdir)
    if bb_lib:
        env["STA_BB_LIB"] = bb_lib
    if driver:
        env["STA_DRIVER"] = driver
    tcl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sta.tcl")
    r = run([sta, "-no_init", "-no_splash", "-exit", tcl], env=env)
    txt = r.stdout + r.stderr
    open(os.path.join(outdir, "sta.rpt"), "w").write(txt)

    # A Liberty can load without error and still be missing the timing model
    # for most of its cells -- see merge_lib.py. OpenSTA says so, in a warning,
    # among thousands of other lines, and the run then reports that every
    # target period closes with zero slack because most gates were modelled as
    # free. That is worse than a failure, so it IS one here.
    blind = len(re.findall(r"no table models found", txt))
    notmpl = len(re.findall(r"table template \S+ not found", txt))
    if blind or notmpl:
        raise SystemExit(
            f"{os.path.basename(lib)} is missing timing data: {blind} timing "
            f"groups with no table model, {notmpl} references to undeclared "
            f"templates. Re-merge the Liberty (syn/yosys/merge_lib.py) -- the "
            f"numbers from this library would be fiction.")
    m = re.search(r"^worst slack max\s+(-?[0-9.]+)", txt, re.M)
    slack = float(m.group(1)) if m else None

    # The worst single stage on the critical path, and its fanout.
    #
    # yosys and abc cannot buffer a net driven by a REGISTER or by a primary
    # input: abc's combinational network begins after the flops, so its
    # `buffer -N` pass never sees those nets. In a complete flow OpenROAD's
    # `repair_design` inserts the buffer tree. Without it, a control signal
    # broadcast to a few hundred sinks keeps whatever gate drives it and the
    # Liberty extrapolates a large delay -- real in kind, wrong in size.
    #
    # So the worst stage is reported beside the path, with its fanout. A
    # critical path whose first stage is a fanout-300 net is a statement about
    # the missing buffer tree; one whose stages are all fanout < 10 is a
    # statement about the design.
    worst_stage = worst_fanout = None
    body = txt.split("==== report_checks -path_delay min", 1)[0]
    for fan, _cap, _slew, delay in re.findall(
            r"^\s*(\d+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+[0-9.]+\s+[v^]\s",
            body, re.M):
        d = float(delay)
        if worst_stage is None or d > worst_stage:
            worst_stage, worst_fanout = d, int(fan)
    return slack, worst_stage, worst_fanout


def parse_stat(log):
    area = cells = flops = None
    for line in log.splitlines():
        m = re.search(r"Chip area for (?:top )?module '\\?\S+': ([0-9.]+)", line)
        if m:
            area = float(m.group(1))
        m = re.search(r"Number of cells:\s+(\d+)", line)
        if m:
            cells = int(m.group(1))
    return area, cells


def parse_flops(premap):
    """Generic sequential cells, counted before technology mapping."""
    if not os.path.exists(premap):
        return None
    txt = open(premap, errors="replace").read()
    return sum(int(n) for n in re.findall(
        r"^\s+\$_(?:S?DFF|DFFSR|DLATCH|SR)\S*\s+(\d+)", txt, re.M))


def one_run(args, lib, design, period_ps):
    """One synth + STA at one target period. Returns the summary dict."""
    outdir = os.path.join(OUT_ROOT, f"{args.top}.{args.pdk}.{args.corner}")
    os.makedirs(outdir, exist_ok=True)
    ys = os.path.join(outdir, "synth.ys")
    with open(ys, "w") as f:
        f.write(yosys_script(design, args.top, lib, period_ps, outdir,
                             args.max_fanout, args.flatten))
    t0 = time.time()
    log = os.path.join(outdir, "yosys.log")
    r = run(["yosys", "-q", "-l", log, ys])
    wall = time.time() - t0
    text = open(log, errors="replace").read()
    if r.returncode != 0 or "ERROR" in text:
        sys.stderr.write("\n".join(
            l for l in text.splitlines() if "ERROR" in l) + "\n")
        raise SystemExit(f"yosys failed for {args.top} -- see {log}")

    area, cells = parse_stat(text)
    flops = parse_flops(os.path.join(outdir, "stat_premap.txt"))
    period_ns = period_ps / 1000.0
    netlist = os.path.join(outdir, "netlist.v")
    bb_lib = blackbox_liberty(netlist, outdir)
    sta_res = opensta(args.top, lib, netlist, period_ns, outdir, args.driver,
                      bb_lib)
    slack, worst_stage, worst_fanout = sta_res if sta_res else (None, None, None)
    achieved = (period_ns - slack) if slack is not None else None
    return {
        "top": args.top, "pdk": args.pdk, "corner": args.corner,
        "target_ps": period_ps, "sram": args.sram,
        "cells": cells, "flops": flops, "area_um2": area,
        "sta_period_ns": period_ns,
        "sta_worst_slack_ns": slack,
        "sta_critical_path_ns": round(achieved, 4) if achieved else None,
        "fmax_mhz": (round(1000.0 / achieved, 1)
                     if achieved and achieved > 0 else None),
        "worst_stage_ns": worst_stage,
        "worst_stage_fanout": worst_fanout,
        "yosys_seconds": round(wall, 1),
        "liberty": os.path.basename(lib),
        "outdir": outdir,
    }


def sweep(args, lib, design):
    """Find the shortest period that still closes.

    A single run at a fixed target reports that it met the target, which is a
    statement about the target rather than about the design: abc stops
    optimising once it is done. The number worth quoting is the shortest period
    the design closes at, so the search drives the target down until it stops
    closing and keeps the last one that did.
    """
    lo, hi = args.sweep_lo, args.sweep_hi
    best = None
    print(f"  sweeping {args.top} on {args.pdk}/{args.corner} "
          f"between {lo} and {hi} ps")
    for i in range(args.sweep_steps):
        mid = (lo + hi) / 2.0
        res = one_run(args, lib, design, round(mid))
        ok = res["sta_worst_slack_ns"] is not None and \
            res["sta_worst_slack_ns"] >= -0.0005
        print(f"    {round(mid):>5} ps target -> "
              f"slack {res['sta_worst_slack_ns']:+.4f} ns  "
              f"{'closes' if ok else 'violates'}")
        if ok:
            best = res
            hi = mid
        else:
            lo = mid
        if hi - lo < args.sweep_resolution:
            break
    if best is None:
        raise SystemExit(
            f"{args.top} did not close even at {args.sweep_hi} ps -- raise "
            f"--sweep-hi, the answer is outside the bracket")
    best["sweep"] = {"lo_ps": lo, "hi_ps": hi,
                     "resolution_ps": args.sweep_resolution}
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", required=True)
    ap.add_argument("--pdk", required=True, choices=("asap7", "sky130"))
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--sweep", action="store_true",
                    help="search for the shortest period that closes")
    ap.add_argument("--sweep-lo", type=float, default=None, dest="sweep_lo")
    ap.add_argument("--sweep-hi", type=float, default=None, dest="sweep_hi")
    ap.add_argument("--sweep-steps", type=int, default=7, dest="sweep_steps")
    ap.add_argument("--sweep-resolution", type=float, default=15.0,
                    dest="sweep_resolution")
    ap.add_argument("--period", type=float, default=500.0,
                    help="abc delay target, in PICOSECONDS for every PDK -- "
                         "abc normalises the Liberty's own time unit")
    ap.add_argument("--no-flatten", action="store_false", dest="flatten",
                    default=True,
                    help="keep the hierarchy. Costs roughly 3x in critical "
                         "path because abc cannot optimise across a module "
                         "boundary; needed for blocks that will not fit in "
                         "memory flattened")
    ap.add_argument("--max-fanout", type=int, default=16,
                    dest="max_fanout",
                    help="fanout cap for abc's buffer pass; matches the "
                         "set_max_fanout in syn/constraints/common.sdc")
    ap.add_argument("--driver", default=None,
                    help="library cell for set_driving_cell in STA")
    ap.add_argument("--sram", default="blackbox",
                    choices=("blackbox", "flops"))
    ap.add_argument("--lib", default=None, help="override the merged Liberty")
    ap.add_argument("--design", default=None,
                    help="a pre-converted Verilog-2005 file, skipping sv2v. "
                         "The black-box substitution is then the caller's "
                         "business, so --sram is ignored.")
    args = ap.parse_args()
    args.sweep_explicit = args.sweep_lo is not None and args.sweep_hi is not None

    pdk = PDKS[args.pdk]
    lib = args.lib or os.environ.get(f"{args.pdk.upper()}_LIB")
    if not lib:
        import glob
        hits = sorted(glob.glob(pdk["lib"].format(corner=args.corner)))
        if not hits:
            raise SystemExit(
                f"No Liberty for {args.pdk}/{args.corner}.\n"
                f"Looked for: {pdk['lib'].format(corner=args.corner)}\n"
                f"Run `make -C syn/yosys pdk-{args.pdk}` to fetch and build "
                f"it, or set {args.pdk.upper()}_LIB.")
        lib = hits[0]
    if not os.path.exists(lib):
        raise SystemExit(f"Liberty not found: {lib}")
    if args.driver is None:
        args.driver = pdk["driver"]
    if not args.sweep_explicit:
        args.sweep_lo, args.sweep_hi = pdk["sweep"]

    design = args.design or sv2v_convert(rtl_sources(args.sram))

    res = (sweep(args, lib, design) if args.sweep
           else one_run(args, lib, design, args.period))

    with open(os.path.join(res["outdir"], "summary.json"), "w") as f:
        json.dump(res, f, indent=2)

    slack = res["sta_worst_slack_ns"]
    fmax = res["fmax_mhz"]
    print(f"{args.top:11s} {args.pdk}/{args.corner}  "
          f"cells {res['cells']:>7}  flops {res['flops']:>6}  "
          f"area {res['area_um2']:>11.1f} um2  "
          f"crit {res['sta_critical_path_ns'] or float('nan'):>7.3f} ns  "
          f"fmax {fmax if fmax else float('nan'):>7.1f} MHz  "
          f"worst stage {res['worst_stage_ns'] or float('nan'):.3f} ns "
          f"@ fanout {res['worst_stage_fanout']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
