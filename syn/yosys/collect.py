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
              "f_max (MHz) |")
        print("| --- | ---: | ---: | ---: | ---: | ---: |")
        for d in items:
            cp = d.get("sta_critical_path_ns")
            fm = d.get("fmax_mhz")
            print(f"| `{d['top']}` | {d['cells']:,} | {d['flops']:,} | "
                  f"{d['area_um2']:,.1f} | "
                  f"{cp if cp is not None else '—'} | "
                  f"{fm if fm is not None else '—'} |")
    print()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
