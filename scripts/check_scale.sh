#!/usr/bin/env bash
#==============================================================================
# check_scale.sh -- elaborate the design at mesh sizes other than the shipped
# one, so that "the parameters are parameters" is a command rather than a claim.
#
# It copies rtl/ and lint/ to a scratch tree, rewrites NUM_TILES / MESH_X /
# MESH_Y in the package, and lints each configuration with -Wall. Nothing else
# in the repository is touched.
#
# What this catches: a width that was a literal chosen for four tiles, a mesh
# wiring expression that only works when the mesh is square, an assertion whose
# bound was sized by hand. Bug B22 was exactly the first of those, and this
# check is what would have caught it the day it was written.
#
# What it does NOT catch: anything functional. Elaborating at 16 tiles says the
# design has 16 tiles' worth of structure, not that the protocol still works.
# That needs the stress tier at 16 tiles, which is a real piece of work and is
# not done -- see docs/verification.md.
#
# Usage: check_scale.sh <verilator> <rtl_dir> <lint_dir> "<src files>"
#==============================================================================
set -u

VERILATOR="$1"; RTL="$2"; LINT="$3"; SRCS="$4"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -r "$RTL" "$TMP/rtl"
cp -r "$LINT" "$TMP/lint"

# tiles mesh_x mesh_y
CONFIGS=("4 2 2" "8 4 2" "16 4 4" "64 8 8")
fail=0

for cfg in "${CONFIGS[@]}"; do
  read -r n x y <<< "$cfg"
  sed -i -E \
    -e "s/(localparam int unsigned NUM_TILES *= *)[0-9]+;/\1$n;/" \
    -e "s/(localparam int unsigned MESH_X *= *)[0-9]+;/\1$x;/" \
    -e "s/(localparam int unsigned MESH_Y *= *)[0-9]+;/\1$y;/" \
    "$TMP/rtl/pkg/coh_pkg.sv"

  printf '  %-24s ' "$n tiles, ${x}x${y} mesh"
  # shellcheck disable=SC2086
  scaled=$(echo $SRCS | tr ' ' '\n' | sed "s|^$RTL|$TMP/rtl|" | tr '\n' ' ')
  if err=$("$VERILATOR" --lint-only -Wall --timing --top-module system_top \
             "$TMP/lint/waivers.vlt" "$TMP/lint/waivers_scale.vlt" \
             $scaled 2>&1); then
    echo "elaborates clean"
  else
    echo "FAIL"
    echo "$err" | grep -E '^%(Error|Warning)' | head -8 | sed 's/^/      /'
    fail=1
  fi
done

exit $fail
