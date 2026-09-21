#!/usr/bin/env python3
"""Apply each RTL mutation in turn and require its named test to fail.

A passing test suite is evidence of nothing by itself. This is the check that
the suite is sensitive to the arcs it claims to cover: break one arc, run the
one test that is supposed to notice, and require it to fail. A mutation that
survives -- the arc is gone and the test still passes -- is an open item.

Usage:
    scripts/mutate.py              # every mutation
    scripts/mutate.py --list
    scripts/mutate.py --only r7-s-d-serves-gets
    scripts/mutate.py --race R3

The RTL is edited in place and restored in a `finally`, so an interrupted run
leaves the tree clean. It refuses to start if the tree already has uncommitted
changes to a file it is going to touch, because then "restored" would be a lie.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from mutations import MUTATIONS, NO_MUTATION  # noqa: E402

TB = REPO / "tb"


def dirty_files() -> set:
    out = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                         capture_output=True, text=True).stdout
    return {line[3:].strip() for line in out.splitlines() if line.strip()}


def run_test(mut) -> tuple[bool, str]:
    """Run the named test. Returns (passed, tail of output)."""
    env = dict(os.environ)
    env["COCOTB_CASE"] = mut["case"]
    # A mutation against the stress tier lowers the request count: the claim is
    # that the test notices, not that it notices only after 100,000 requests,
    # and a full-length run per mutation would make `make mutate` unusable.
    env.update(mut.get("env", {}))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", mut["test"], "-q", "--no-header"],
        cwd=str(TB), env=env, capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, "\n".join(tail[-3:])


def apply_one(mut) -> tuple[str, str]:
    """Mutate, test, restore. Returns (verdict, detail)."""
    path = REPO / mut["file"]
    original = path.read_text()
    count = original.count(mut["find"])
    if count != 1:
        return "BROKEN", (
            f"the text to replace appears {count} times in {mut['file']}; a "
            f"mutation must name exactly one site")
    backup = Path(tempfile.mkdtemp()) / path.name
    shutil.copy2(path, backup)
    try:
        path.write_text(original.replace(mut["find"], mut["replace"]))
        t0 = time.time()
        passed, tail = run_test(mut)
        dt = time.time() - t0
        if passed:
            return "SURVIVED", (
                f"{mut['case']} still passed in {dt:.0f}s with the arc "
                f"removed -- the test is not sensitive to it")
        return "KILLED", f"{mut['case']} failed in {dt:.0f}s: {tail.splitlines()[-1]}"
    finally:
        shutil.copy2(backup, path)
        shutil.rmtree(backup.parent, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", help="run one mutation by name")
    ap.add_argument("--race", help="run every mutation for one race, e.g. R3")
    args = ap.parse_args()

    muts = MUTATIONS
    if args.only:
        muts = [m for m in muts if m["name"] == args.only]
    if args.race:
        muts = [m for m in muts if m["race"].upper() == args.race.upper()]
    if not muts:
        print("no mutations selected", file=sys.stderr)
        return 2

    if args.list:
        for m in MUTATIONS:
            print(f"{m['race']:>4}  {m['name']:<34} {m['file']}  -> {m['case']}")
        for race, why in NO_MUTATION.items():
            print(f"{race:>4}  {'(none)':<34} {why}")
        return 0

    touched = {m["file"] for m in muts}
    clash = touched & dirty_files()
    if clash:
        print("refusing to run: uncommitted changes in " + ", ".join(sorted(clash)),
              file=sys.stderr)
        print("commit or stash them first -- this script restores from its own "
              "backup, not from git, and a crash mid-run would lose them.",
              file=sys.stderr)
        return 2

    results = []
    for m in muts:
        print(f"== {m['race']} {m['name']} ", flush=True)
        verdict, detail = apply_one(m)
        print(f"   {verdict}: {detail}", flush=True)
        results.append((m, verdict, detail))

    print()
    print(f"{'race':>4}  {'mutation':<34} {'verdict':<9} test")
    for m, verdict, _ in results:
        print(f"{m['race']:>4}  {m['name']:<34} {verdict:<9} {m['case']}")
    for race, why in NO_MUTATION.items():
        if not args.only and not args.race:
            print(f"{race:>4}  {'(none)':<34} {'n/a':<9} {why}")

    bad = [m["name"] for m, v, _ in results if v != "KILLED"]
    if bad:
        print(f"\n{len(bad)} mutation(s) not killed: {', '.join(bad)}")
        return 1
    print(f"\nall {len(results)} mutations killed by their named test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
