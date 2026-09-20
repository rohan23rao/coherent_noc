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

# Emit "file:line:code" for every line under $RTL_DIR with // comments blanked.
# Without this the checks match their own explanatory comments -- a header that
# says "force an exact interleaving" is not a `force` statement, and a checker
# that cannot tell the difference trains people to ignore it.
code_lines() {
  find "$RTL_DIR" -name '*.sv' -print0 \
    | xargs -0 awk '{
        line = $0;
        note = (index($0, "no-reset:") > 0) ? " NORESET" : "";
        sub(/\/\/.*/, "", line);
        printf "%s:%d:%s%s\n", FILENAME, FNR, line, note
      }'
}

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
hits=$(code_lines | grep -E '\b(logic|wire|reg|bit)[[:space:]]*\[[^]:]*\]' \
       | grep -vE '\[[^]]*:' || true)
report "packed dimension must be [N-1:0], not [N]" "$hits"

# 2. Simulation-only constructs.
hits=$(code_lines | grep -E ':[[:space:]]*initial\b|\bforce\b|\brelease\b|#[[:space:]]*[0-9]' || true)
report "initial / #delay / force / release are not allowed under rtl/" "$hits"

# 3. Every always_ff must take negedge rst_n, or carry an explicit
#    "no-reset:" justification on the same line. This is the invariant that
#    Verilator's SYNCASYNCNET is a proxy for; SYNCASYNCNET itself is waived
#    because `disable iff (!rst_n)` in SVA trips it unconditionally.
hits=$(code_lines | grep -E 'always_ff[[:space:]]*@[[:space:]]*\([[:space:]]*posedge' \
       | grep -vE 'negedge a?rst_n' | grep -v 'NORESET' || true)
report "always_ff must use 'or negedge rst_n' or carry a 'no-reset:' note" "$hits"

# 4. Bare always blocks.
hits=$(code_lines | grep -E '\balways[[:space:]]*@' || true)
report "use always_ff / always_comb, not bare always @" "$hits"

if [ "$fail" -eq 0 ]; then
  echo "style: clean"
fi
exit "$fail"
