#!/usr/bin/env python3
"""Collect every summary.json into one table, for docs/synthesis.md.

Prints Markdown. The columns are chosen so that the numbers cannot be read as
more than they are: the flop count sits next to the area because in this design
it explains most of it, and the critical path is labelled with the corner and
the target it was swept to.
"""
import glob
import json
import os
import sys


def rows(outdir):
    for f in sorted(glob.glob(os.path.join(outdir, "*", "summary.json"))):
        with open(f) as fh:
            yield json.load(fh)


def render(outdir):
    """The table, as a string, so it can be printed or injected."""
    import io
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        main(outdir)
    finally:
        sys.stdout = real
    return buf.getvalue().strip()


def inject(doc, outdir):
    """Rewrite the table between the markers in docs/synthesis.md."""
    import re
    table = render(outdir)
    text = open(doc).read()
    pat = re.compile(r"<!-- BEGIN results -->.*?<!-- END results -->", re.S)
    if not pat.search(text):
        raise SystemExit(f"{doc}: no <!-- BEGIN results --> markers")
    text = pat.sub("<!-- BEGIN results -->\n" + table + "\n"
                   "<!-- END results -->", text)
    open(doc, "w").write(text)
    print(f"{doc}: results table updated "
          f"({len(list(rows(outdir)))} runs)")


def main(outdir):
    data = list(rows(outdir))
    if not data:
        raise SystemExit(f"no summary.json under {outdir} -- run the flow first")

    by_pdk = {}
    for d in data:
        by_pdk.setdefault((d["pdk"], d["corner"]), []).append(d)

    order = {b: i for i, b in enumerate(
        ["tile_nic", "dir_ctrl", "l1_cache", "router", "tile_top",
         "system_top"])}

    for (pdk, corner), items in sorted(by_pdk.items()):
        items.sort(key=lambda d: order.get(d["top"], 99))
        lib = items[0]["liberty"]
        print(f"\n### {pdk} — {corner} corner\n")
        print(f"`{lib}`, arrays black-boxed, SRAM = "
              f"{items[0]['sram']}.\n")
        print("| block | cells | flops | area (um^2) | critical path (ns) | "
              "f_max (MHz) | worst stage (ns) | its fanout |")
        print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for d in items:
            cp = d.get("sta_critical_path_ns")
            fm = d.get("fmax_mhz")
            ws = d.get("worst_stage_ns")
            wf = d.get("worst_stage_fanout")
            print(f"| `{d['top']}` | {d['cells']:,} | {d['flops']:,} | "
                  f"{d['area_um2']:,.1f} | "
                  f"{f'{cp:.3f}' if cp is not None else '—'} | "
                  f"{f'{fm:.0f}' if fm is not None else '—'} | "
                  f"{f'{ws:.3f}' if ws is not None else '—'} | "
                  f"{wf if wf is not None else '—'} |")
        print("\nThe last two columns are the flow's limitation, not the "
              "design's: abc cannot buffer a register-driven net and there is "
              "no `repair_design` here, so a high-fanout control signal keeps "
              "whatever single gate drives it. Read the critical path as an "
              "upper bound and `critical path - worst stage` as a rough "
              "floor.")
    print()


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    if len(sys.argv) > 2 and sys.argv[1] == "--inject":
        inject(sys.argv[2], here)
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else here)
