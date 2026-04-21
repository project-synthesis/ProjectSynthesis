#!/usr/bin/env python3
"""One-shot recovery: clean taxonomy debris left over from bulk optimization deletes.

Removes:
- prompt_cluster rows with state='archived' AND member_count=0 (and their meta_patterns).
- orphan meta_patterns whose cluster_id no longer exists.
- orphan optimization_patterns pointing at missing clusters/optimizations.

Preserves project nodes (state='project') and the canonical 'general' domain node.

Idempotent — safe to re-run. Run with backend STOPPED to avoid racing the engine:

    ./init.sh stop
    python3 scripts/reset_taxonomy.py
    ./init.sh start
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "synthesis.db"


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    summary: dict[str, int] = {}

    archived = cur.execute(
        "SELECT id, label FROM prompt_cluster WHERE state='archived' AND member_count=0"
    ).fetchall()
    archived_ids = [r["id"] for r in archived]
    if archived_ids:
        safe_ids: list[str] = []
        for cid in archived_ids:
            still_referenced = cur.execute(
                "SELECT 1 FROM optimizations WHERE cluster_id=? LIMIT 1", (cid,)
            ).fetchone()
            if still_referenced:
                continue
            has_children = cur.execute(
                "SELECT 1 FROM prompt_cluster WHERE parent_id=? LIMIT 1", (cid,)
            ).fetchone()
            if has_children:
                continue
            has_op_patterns = cur.execute(
                "SELECT 1 FROM optimization_patterns WHERE cluster_id=? LIMIT 1", (cid,)
            ).fetchone()
            if has_op_patterns:
                continue
            safe_ids.append(cid)

        if safe_ids:
            placeholders = ",".join("?" * len(safe_ids))
            cur.execute(
                f"DELETE FROM meta_patterns WHERE cluster_id IN ({placeholders})",
                safe_ids,
            )
            summary["meta_patterns_deleted"] = cur.rowcount
            cur.execute(
                f"DELETE FROM prompt_cluster WHERE id IN ({placeholders})",
                safe_ids,
            )
            summary["archived_clusters_deleted"] = cur.rowcount
            print(
                f"Deleted archived clusters: {[r['label'] for r in archived if r['id'] in safe_ids]}"
            )

    orphan_metas = cur.execute(
        """
        DELETE FROM meta_patterns
        WHERE cluster_id NOT IN (SELECT id FROM prompt_cluster)
        """
    )
    if orphan_metas.rowcount:
        summary["orphan_meta_patterns_deleted"] = orphan_metas.rowcount

    orphan_ops = cur.execute(
        """
        DELETE FROM optimization_patterns
        WHERE cluster_id NOT IN (SELECT id FROM prompt_cluster)
           OR optimization_id NOT IN (SELECT id FROM optimizations)
        """
    )
    if orphan_ops.rowcount:
        summary["orphan_optimization_patterns_deleted"] = orphan_ops.rowcount

    conn.commit()
    conn.close()

    index_cache = DB_PATH.parent / "embedding_index.pkl"
    if index_cache.exists():
        index_cache.unlink()
        summary["embedding_index_cache_cleared"] = 1

    if not summary:
        print("Nothing to clean — taxonomy state is already consistent.")
        return 0

    print("Reset summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\nRestart the backend to rebuild indices: ./init.sh restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
