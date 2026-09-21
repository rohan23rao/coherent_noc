#!/usr/bin/env python3
"""Dump the port names of one module, for the synthesis dry run.

The dry run stubs out every Design Compiler command, but `get_ports` has to
answer truthfully or the SDC's `-quiet` branches all take the same path and the
check proves nothing. So the port list comes from the RTL itself, via
Verilator's elaborated XML, with SYNTHESIS defined -- the same configuration
dc_shell reads.

Usage: ports.py <top> <filelist> [<verilator>]
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import os


def ports_of(top, filelist, verilator="verilator"):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    srcs = [l.strip() for l in open(filelist) if l.strip()
            and not l.startswith("#")]
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        xml = f.name
    cmd = [verilator, "--xml-only", "--timing", "-DSYNTHESIS",
           "-Wno-DEPRECATED", "--top-module", top,
           os.path.join(root, "lint", "waivers.vlt"),
           os.path.join(root, "lint", "waivers_synth.vlt"),
           *srcs, "--xml-output", xml]
    subprocess.run(cmd, capture_output=True)
    tree = ET.parse(xml)
    os.unlink(xml)
    for m in tree.iter("module"):
        if m.get("name") == top:
            return [v.get("name") for v in m.iter("var") if v.get("dir")]
    raise SystemExit(f"ports.py: module {top} not found in the elaborated XML")


if __name__ == "__main__":
    top, filelist = sys.argv[1], sys.argv[2]
    ver = sys.argv[3] if len(sys.argv) > 3 else "verilator"
    print("\n".join(ports_of(top, filelist, ver)))
