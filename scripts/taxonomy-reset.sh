#!/usr/bin/env bash
# Fresh-start reset for the taxonomy graph (Hybrid architecture).
#
# Wipes every derived taxonomy entity (clusters, patterns, snapshots) so the
# warm/cold paths can re-emerge a fresh graph from the preserved prompt data.
# Use this after a breaking taxonomy change, or to reset a polluted small-DB
# taxonomy back to a clean "organic emergence" state.
#
# WIPES:
#   - prompt_cluster        (projects/domains/sub-domains/clusters)
#   - meta_patterns         (cluster-anchored reusable techniques)
#   - global_patterns       (cross-project durable patterns)
#   - optimization_patterns (pattern↔optimization provenance join)
#   - taxonomy_snapshots    (audit trail)
#   - data/taxonomy_events/ (decision JSONL history)
#
# PRESERVES:
#   - optimizations    (user prompt history — cluster_id/project_id nulled)
#   - feedbacks        (user ratings)
#   - linked_repos     (GitHub repo links — project_node_id nulled)
#   - github_tokens    (OAuth state)
#   - repo_file_index  (indexed codebase content)
#   - refinement_*     (refinement branches + turns)
#   - strategy_affinities
#   - prompt_templates (immutable forks; source_cluster_id nulled)
#   - audit_log
#
# Usage:
#   ./scripts/taxonomy-reset.sh --dry-run   # show what would change
#   ./scripts/taxonomy-reset.sh --confirm   # actually reset
#
# The script refuses to run without --confirm unless --dry-run is passed.
# A timestamped backup of data/synthesis.db is written to data/backups/
# before any destructive step.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
cd "$ROOT"

DRY_RUN=false
CONFIRMED=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --confirm) CONFIRMED=true ;;
        -h|--help)
            head -n 36 "$0" | tail -n 34 | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown argument '$arg'" >&2
            echo "usage: $0 [--dry-run] [--confirm]" >&2
            exit 2
            ;;
    esac
done

if ! $DRY_RUN && ! $CONFIRMED; then
    cat >&2 <<'EOF'
refused: this script wipes the taxonomy graph and cannot be undone
         cleanly without the pre-run backup.

    run with --dry-run to preview, or --confirm to proceed.
EOF
    exit 2
fi

DB_PATH="$ROOT/data/synthesis.db"
EVENTS_DIR="$ROOT/data/taxonomy_events"
BACKUP_DIR="$ROOT/data/backups"

if [[ ! -f "$DB_PATH" ]]; then
    echo "error: database not found at $DB_PATH" >&2
    exit 1
fi

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "error: $PY is required but not on PATH" >&2
    exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="$BACKUP_DIR/synthesis.db.$TS.pre-taxonomy-reset.bak"

echo "== taxonomy reset =="
echo "db         : $DB_PATH"
echo "events dir : $EVENTS_DIR"
echo "backup to  : $BACKUP_FILE"
echo "mode       : $([ "$DRY_RUN" = true ] && echo 'DRY-RUN' || echo 'DESTRUCTIVE')"
echo

count_rows() {
    local table="$1"
    "$PY" - "$DB_PATH" "$table" <<'PY' 2>/dev/null || echo "?"
import sqlite3, sys
db, table = sys.argv[1], sys.argv[2]
try:
    c = sqlite3.connect(db)
    print(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
except Exception:
    print("?")
PY
}

echo "current state:"
printf "  prompt_cluster        : %s\n" "$(count_rows prompt_cluster)"
printf "  meta_patterns         : %s\n" "$(count_rows meta_patterns)"
printf "  global_patterns       : %s\n" "$(count_rows global_patterns)"
printf "  optimization_patterns : %s\n" "$(count_rows optimization_patterns)"
printf "  taxonomy_snapshots    : %s\n" "$(count_rows taxonomy_snapshots)"
printf "  optimizations         : %s (preserved)\n" "$(count_rows optimizations)"
printf "  linked_repos          : %s (preserved)\n" "$(count_rows linked_repos)"
printf "  prompt_templates      : %s (preserved)\n" "$(count_rows prompt_templates)"
echo

if $DRY_RUN; then
    cat <<'EOF'
DRY-RUN: no changes written.

On --confirm the script will:
  1. Copy the DB to data/backups/ with a UTC timestamp.
  2. BEGIN TRANSACTION; DELETE FROM the 5 taxonomy tables.
  3. NULL Optimization.cluster_id, Optimization.project_id.
  4. NULL LinkedRepo.project_node_id.
  5. NULL PromptTemplate.source_cluster_id, PromptTemplate.source_optimization_id.
  6. COMMIT; VACUUM.
  7. Move data/taxonomy_events/ aside to events.$TS/ so the ring buffer starts empty.

Next startup will:
  - Recreate the Legacy project node (main.py ADR-005 migration).
  - Backfill Optimization.project_id from repo_full_name → LinkedRepo.
  - Emerge a fresh organic taxonomy as warm/cold paths run.
EOF
    exit 0
fi

mkdir -p "$BACKUP_DIR"
cp -a "$DB_PATH" "$BACKUP_FILE"
echo "backup written → $BACKUP_FILE"

"$PY" - "$DB_PATH" <<'PY'
import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(db)
con.isolation_level = None  # manual tx control
cur = con.cursor()
cur.execute("PRAGMA foreign_keys = OFF;")
try:
    cur.execute("BEGIN IMMEDIATE;")
    cur.execute("DELETE FROM optimization_patterns;")
    cur.execute("DELETE FROM taxonomy_snapshots;")
    cur.execute("DELETE FROM meta_patterns;")
    cur.execute("DELETE FROM global_patterns;")
    cur.execute("DELETE FROM prompt_cluster;")
    cur.execute("UPDATE optimizations SET cluster_id = NULL, project_id = NULL;")
    cur.execute("UPDATE linked_repos SET project_node_id = NULL;")
    cur.execute(
        "UPDATE prompt_templates "
        "SET source_cluster_id = NULL, source_optimization_id = NULL;"
    )
    cur.execute("COMMIT;")
except Exception as exc:
    cur.execute("ROLLBACK;")
    raise SystemExit(f"reset failed: {exc}")
finally:
    cur.execute("PRAGMA foreign_keys = ON;")
cur.execute("VACUUM;")
con.close()
print("tables wiped + FKs nulled.")
PY

if [[ -d "$EVENTS_DIR" ]]; then
    NEW_PATH="$ROOT/data/taxonomy_events.$TS.archived"
    mv "$EVENTS_DIR" "$NEW_PATH"
    echo "event log archived → $NEW_PATH"
fi

echo
echo "reset complete. restart the backend to re-emerge the taxonomy:"
echo "    ./init.sh restart"
