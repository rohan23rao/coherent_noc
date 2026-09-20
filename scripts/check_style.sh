#!/usr/bin/env bash
# Structural conventions Verilator does not check. Run as part of `make lint`.
#
#   1. Packed dimensions must be written [N-1:0], never [N].
#   2. No `initial`, no `#<delay>`, no force/release anywhere under rtl/.
#   3. No bare `always @(...)` -- always_ff / always_comb / always_latch only.
#
# Exits non-zero on the first category that has hits, printing file:line.
set -uo pipefail

RTL_DIR="${1:-rtl}"
fail=0

report() {
  local label="$1"; shift
  local hits="$1"; shift
  if [ -n "$hits" ]; then
    echo "STYLE FAIL: $label"
    echo "$hits" | sed 's/^/  /'
    fail=1
  fi
}

# 1. Packed dimension with no colon directly after a type keyword.
hits=$(grep -rnE '\b(logic|wire|reg|bit)\s*\[[^]:]*\]' "$RTL_DIR" --include='*.sv' \
       | grep -vE '\[[^]]*:' || true)
report "packed dimension must be [N-1:0], not [N]" "$hits"

# 2. Simulation-only constructs.
hits=$(grep -rnE '^\s*initial\b|\bforce\b|\brelease\b|#\s*[0-9]' "$RTL_DIR" --include='*.sv' || true)
report "initial / #delay / force / release are not allowed under rtl/" "$hits"

# 3. Bare always blocks.
hits=$(grep -rnE '\balways\s*@' "$RTL_DIR" --include='*.sv' || true)
report "use always_ff / always_comb, not bare always @" "$hits"

if [ "$fail" -eq 0 ]; then
  echo "style: clean"
fi
exit "$fail"
