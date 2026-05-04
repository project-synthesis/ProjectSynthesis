#!/usr/bin/env bash
# v0.4.14 acceptance: drive REAL migrated handlers concurrently and verify zero
# audit warnings + zero locks in data/backend.log. Run after init.sh restart.
set -euo pipefail

LOG="data/backend.log"
BASELINE_LOCK=$(grep -c "database is locked" "$LOG" 2>/dev/null || echo 0)
BASELINE_AUDIT=$(grep -c "read-engine audit:" "$LOG" 2>/dev/null || echo 0)

echo "Baseline: $BASELINE_LOCK locks, $BASELINE_AUDIT audit warns"

# Concurrent driver — exercise each migrated path
{
  # 5 MCP optimize-passthrough (force passthrough)
  for i in $(seq 1 5); do
    curl -fsS -X POST http://127.0.0.1:8001/mcp \
      -H "Content-Type: application/json" \
      -d "{\"method\":\"tools/call\",\"params\":{\"name\":\"synthesis_optimize\",\"arguments\":{\"prompt\":\"smoke $i\",\"force_passthrough\":true}}}" \
      > /dev/null &
  done

  # 5 strategy_updated audit-log writes
  for i in $(seq 1 5); do
    curl -fsS -X PUT "http://127.0.0.1:8000/api/strategies/auto" \
      -H "Content-Type: application/json" \
      -d "{\"content\":\"# smoke $i\\nstrategy: auto\"}" \
      > /dev/null &
  done

  # 5 api_key set/delete cycles
  for i in $(seq 1 5); do
    curl -fsS -X PATCH "http://127.0.0.1:8000/api/provider/api-key" \
      -H "Content-Type: application/json" \
      -d "{\"api_key\":\"sk-smoketest-$i\"}" > /dev/null
    curl -fsS -X DELETE "http://127.0.0.1:8000/api/provider/api-key" > /dev/null
  done

  wait
}

NEW_LOCK=$(grep -c "database is locked" "$LOG" 2>/dev/null || echo 0)
NEW_AUDIT=$(grep -c "read-engine audit:" "$LOG" 2>/dev/null || echo 0)

DELTA_LOCK=$((NEW_LOCK - BASELINE_LOCK))
DELTA_AUDIT=$((NEW_AUDIT - BASELINE_AUDIT))

echo "Delta: $DELTA_LOCK new locks, $DELTA_AUDIT new audit warns"
[ "$DELTA_LOCK" -eq 0 ] || { echo "FAIL: $DELTA_LOCK new locks"; exit 1; }
[ "$DELTA_AUDIT" -eq 0 ] || { echo "FAIL: $DELTA_AUDIT new audit warns"; exit 1; }
echo "PASS"
