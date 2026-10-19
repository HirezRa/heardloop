#!/usr/bin/env bash
# Unit-style checks for update-script decision matrix (no Docker required).
# Run: bash scripts/test_update_decision.sh
set -euo pipefail

decision() {
  local running="$1" files="$2" target="$3"
  local need_checkout=0 need_rebuild=0
  [[ "$files" != "$target" ]] && need_checkout=1
  if [[ -z "$running" || "$running" != "$target" ]]; then
    need_rebuild=1
  fi
  if [[ "$need_rebuild" -eq 0 ]]; then
    echo up_to_date
  elif [[ "$need_checkout" -eq 0 ]]; then
    echo rebuild_only
  else
    echo checkout_and_rebuild
  fi
}

fail=0
check() {
  local name="$1" running="$2" files="$3" target="$4" expect="$5"
  local got
  got="$(decision "$running" "$files" "$target")"
  if [[ "$got" != "$expect" ]]; then
    echo "FAIL ${name}: got=${got} expect=${expect}"
    fail=$((fail + 1))
  else
    echo "ok   ${name} -> ${got}"
  fi
}

check "running older + files current" "v0.9.0" "v0.9.1" "v0.9.1" "rebuild_only"
check "all equal" "v0.9.1" "v0.9.1" "v0.9.1" "up_to_date"
check "stack down" "" "v0.9.1" "v0.9.1" "rebuild_only"
check "files behind" "v0.9.0" "v0.9.0" "v0.9.1" "checkout_and_rebuild"

[[ "$fail" -eq 0 ]] || exit 1
echo "All decision cases passed."
