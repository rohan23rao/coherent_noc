#!/usr/bin/env bash
#==============================================================================
# fetch_pdk.sh -- get the standard-cell Liberty for one PDK, from its source.
#
# Both PDKs here are free, which is the entire point of this flow: the Design
# Compiler flow in syn/ cannot run without a licence and a kit, so it has never
# produced a number. This one can, so it has.
#
#   asap7    7nm predictive, ASU / OpenROAD mirror. Ships its cells split
#            across five functional groups and compressed with 7-zip, so this
#            extracts them and merges them into one Liberty per corner --
#            abc will only read one. Corners: tt (0.70 V, 25 C) and
#            ss (0.63 V, 100 C).
#   sky130   SkyWater 130nm. The skywater-pdk repository stores timing as
#            .lib.json fragments that need its own build flow to assemble, so
#            this takes the already-assembled Liberty from OpenROAD-flow-
#            scripts instead. Only the tt corner (1.80 V, 25 C) is published
#            there; ss needs open_pdks or volare.
#
# Usage: fetch_pdk.sh asap7|sky130 [install_root]      (default /opt/pdk)
#==============================================================================
set -euo pipefail

PDK="${1:?usage: fetch_pdk.sh asap7|sky130 [root]}"
ROOT="${2:-/opt/pdk}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

need() { command -v "$1" >/dev/null || { echo "missing tool: $1" >&2; exit 1; }; }

case "$PDK" in
asap7)
  need git; need 7z; need python3
  SRC="$ROOT/asap7/src"
  mkdir -p "$ROOT/asap7/lib" "$ROOT/asap7/merged"
  if [ ! -d "$SRC/.git" ]; then
    # --filter=blob:none keeps the GDS and the SPICE out of the download; only
    # the Liberty archives are actually fetched, by the checkout below.
    git clone --depth 1 --filter=blob:none \
      https://github.com/The-OpenROAD-Project/asap7sc7p5t_28.git "$SRC"
  fi
  for corner in TT SS; do
    for group in SIMPLE INVBUF AO OA SEQ; do
      f=$(ls "$SRC"/LIB/NLDM/asap7sc7p5t_${group}_RVT_${corner}_nldm_*.lib.7z 2>/dev/null | head -1)
      [ -n "$f" ] || { echo "not found: ${group}_RVT_${corner}" >&2; exit 1; }
      7z x -y -o"$ROOT/asap7/lib" "$f" >/dev/null
    done
    lc=$(echo "$corner" | tr '[:upper:]' '[:lower:]')
    python3 "$HERE/merge_lib.py" "$ROOT/asap7/merged/asap7_rvt_${lc}.lib" \
      $(ls "$ROOT"/asap7/lib/*RVT_${corner}*.lib)
  done
  ;;
sky130)
  need curl
  mkdir -p "$ROOT/sky130/merged"
  base=https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/master/flow/platforms/sky130hd/lib
  f=sky130_fd_sc_hd__tt_025C_1v80.lib
  curl -fsSL -o "$ROOT/sky130/merged/$f" "$base/$f"
  echo "$ROOT/sky130/merged/$f: $(grep -c '^  cell ' "$ROOT/sky130/merged/$f") cells (1.80 V, 25 C)"
  ;;
*)
  echo "unknown PDK: $PDK (expected asap7 or sky130)" >&2; exit 1 ;;
esac

echo "$PDK ready under $ROOT/$PDK"
