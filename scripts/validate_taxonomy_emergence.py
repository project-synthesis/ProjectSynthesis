"""E2E validation harness — drives organic domain + sub-domain emergence.

Submits a curated set of prompts ABOUT the codebase to /api/optimize so the
analyzer + clustering + warm-path observe a tight semantic neighbourhood
and ideally promote a sub-domain. After each cycle:
  - Pulls /api/clusters/tree for cluster topology
  - Pulls /api/domains/readiness for stability + emergence per domain
  - Pulls /api/clusters/activity for the live event ring buffer

Usage:
    python scripts/validate_taxonomy_emergence.py CYCLE_NAME

Designed to be run repeatedly so we can fine-tune the prompt set per cycle
based on observed top_qualifier / consistency gap. Persists each cycle's
readiness snapshot to data/validation/<cycle>.json for diff against later
runs.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = os.environ.get("SYNTHESIS_API", "http://localhost:8000")
PROJECT_ID = os.environ.get(
    "SYNTHESIS_PROJECT_ID", "3f0437ed-5503-41e1-9def-00c54f21f46f",
)


def _post(path: str, body: dict, timeout: float = 600.0) -> dict | None:
    """POST JSON, parse response. SSE responses surface as text — this
    helper consumes the stream and returns the final ``optimization_complete``
    payload when present."""
    req = Request(
        f"{API}{path}",
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    final: dict | None = None
    try:
        with urlopen(req, timeout=timeout) as resp:
            buf = b""
            for chunk in resp:
                buf += chunk
                while b"\n\n" in buf:
                    block, buf = buf.split(b"\n\n", 1)
                    lines = block.decode("utf-8", errors="replace").splitlines()
                    event = next((l[7:].strip() for l in lines if l.startswith("event: ")), "")
                    data_line = next((l[6:] for l in lines if l.startswith("data: ")), "")
                    if not data_line:
                        continue
                    try:
                        payload = json.loads(data_line)
                    except json.JSONDecodeError:
                        continue
                    if event in ("optimization_complete", "optimization_error", "passthrough"):
                        final = {"event": event, "payload": payload}
        return final
    except HTTPError as exc:
        return {"event": "http_error", "status": exc.code, "body": exc.read().decode("utf-8", "replace")[:300]}
    except (URLError, TimeoutError) as exc:
        return {"event": "network_error", "error": str(exc)}


def _get(path: str) -> dict | list:
    with urlopen(f"{API}{path}", timeout=15) as resp:
        return json.loads(resp.read())


def submit_prompt(text: str, idx: int, total: int) -> dict:
    """Fire one prompt through the optimize pipeline."""
    print(f"  [{idx:2d}/{total}] {text[:75]}...", flush=True)
    t0 = time.time()
    body = {
        "prompt": text,
        "strategy": "auto",
        "project_id": PROJECT_ID,
    }
    out = _post("/api/optimize", body)
    dt = time.time() - t0
    if out and out.get("event") == "optimization_complete":
        p = out["payload"]
        score = p.get("overall_score", "?")
        intent = p.get("intent_label", "?")
        print(f"      → {dt:5.1f}s  score={score}  intent={intent!r}", flush=True)
    elif out:
        print(f"      → {dt:5.1f}s  event={out.get('event')}  {str(out)[:120]}", flush=True)
    return out or {}


def snapshot(cycle_name: str) -> dict:
    """Pull cluster tree + readiness + activity counts."""
    tree = _get("/api/clusters/tree")
    readiness = _get("/api/domains/readiness")
    activity = _get("/api/clusters/activity?limit=50")
    history = _get("/api/history?limit=1")
    snap = {
        "cycle": cycle_name,
        "ts": datetime.now(timezone.utc).isoformat(),
        "tree": tree,
        "readiness": readiness,
        "activity_recent_count": len(activity.get("events", [])) if isinstance(activity, dict) else 0,
        "total_optimizations": history.get("total", 0) if isinstance(history, dict) else 0,
    }
    out_dir = Path("data/validation")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{cycle_name}.json"
    out_path.write_text(json.dumps(snap, indent=2))
    return snap


def print_summary(snap: dict) -> None:
    """Render a compact human report from a snapshot."""
    print()
    print("=" * 80)
    print(f"  CYCLE SNAPSHOT — {snap['cycle']} @ {snap['ts']}")
    print("=" * 80)
    nodes = snap["tree"].get("nodes", []) if isinstance(snap["tree"], dict) else []
    domain_nodes = [n for n in nodes if n["state"] == "domain"]
    cluster_nodes = [n for n in nodes if n["state"] in ("active", "candidate", "mature")]
    print(f"\n  Total optimizations: {snap['total_optimizations']}  | Domains: {len(domain_nodes)}  | Live clusters: {len(cluster_nodes)}\n")

    print("  CLUSTERS:")
    for n in sorted(cluster_nodes, key=lambda x: -x.get("member_count", 0)):
        print(f"    {n['state']:9s} {n.get('domain', '?'):14s}"
              f"  '{n['label'][:50]}'  m={n.get('member_count',0)}"
              f"  coh={n.get('coherence') or 0:.2f}")

    print("\n  DOMAIN READINESS:")
    print(f"    {'domain':14s} {'mem':>4s} {'sub':>4s} | "
          f"{'stab_tier':>10s} {'cons':>5s} | {'emerg_tier':>10s} {'thresh':>6s} {'top_qualifier':25s} {'gap':>7s}")
    for r in (snap["readiness"] if isinstance(snap["readiness"], list) else []):
        s = r.get("stability") or {}
        e = r.get("emergence") or {}
        top = e.get("top_candidate") or "—"
        if isinstance(top, dict):
            top = top.get("qualifier") or "—"
        gap = e.get("gap_to_threshold")
        gap_s = f"{gap:.3f}" if isinstance(gap, (int, float)) else "—"
        print(f"    {r['domain_label']:14s} {r['member_count']:>4d} {s.get('sub_domain_count', 0):>4d} | "
              f"{str(s.get('tier','?')):>10s} {s.get('consistency',0):>5.2f} | "
              f"{str(e.get('tier','?')):>10s} {e.get('threshold',0):>6.2f} {str(top)[:25]:25s} {gap_s:>7s}")
    print()


# ----------------------------------------------------------------------------
# Prompt sets — mutate per cycle to push specific qualifiers
# ----------------------------------------------------------------------------

PROMPT_SETS = {
    # Cycle 1: backend / async-audit qualifier, 12 prompts to push backend > 5 members
    "cycle-1-async-audit": [
        "Audit the asyncio.gather error handling in our warm-path Phase 4 — find race conditions where a transient failure poisons the maintenance transaction.",
        "Diagnose the SQLAlchemy AsyncSession lifetime in our cross-process taxonomy_changed event bridge — when does session.close() race with a pending await?",
        "Review the asyncio.to_thread usage in EmbeddingService.aembed_single — is the threadpool exhausted under bursty match_prompt traffic?",
        "Audit our background task GC in main.py — find weak-ref races where _spawn_bg_task lets link_repo / reindex jobs disappear mid-await.",
        "Trace the await chain through warm Phase 5 sub_domain re-evaluation — flag any place where dissolution races with a concurrent classification.",
        "Find race conditions in the SSE EventSource reconnection logic — when does sseHealthStore.connect collide with a pending degradation toast?",
        "Audit the asyncio.Queue draining pattern across event_bus subscribers — find the off-by-one where get_nowait races with put_nowait.",
        "Diagnose the warm-path debounce timer race — when does taxonomy_changed fire BEFORE _apply_cross_process_dirty_marks finishes?",
        "Audit asyncio.Lock usage in RoutingManager — find the path where _state read happens without holding the lock.",
        "Review the async generator close() semantics in our SSE response wrappers — are we leaking generator state on client disconnect?",
        "Find the race between alembic upgrade and lifespan startup where _gc_orphan_meta_patterns runs against an unmigrated DB.",
        "Audit the asyncio.gather + return_exceptions=True call in batch_orchestrator — confirm partial failure does not leave the dirty_set inconsistent.",
    ],
    # Cycle 11 — single-prompt B5+ end-to-end verification.  This is
    # designed to exercise EVERY B5/B5+ component in one shot:
    #   - "Write" lead verb → B5+ task-type lock should preserve
    #     task_type=writing even though the body has heavy code refs
    #     (LLM analyzer would otherwise flip to coding, as it did on
    #     cycle-10 prompt 1).
    #   - Heavy inline-backtick file paths in the body → B5 full-prompt
    #     technical-signal rescue should fire (technical_signals_detected
    #     should be False on first sentence; full_prompt_technical_rescue
    #     should be True).
    #   - Codebase context delivered but TRIMMED to ≤15K chars
    #     (codebase_context_trimmed_for_writing populated in
    #     enrichment_meta).  Prevents the cycle-10 prompt-1 hallucination
    #     class (where 80K of code led the optimizer to fish for
    #     plausible-but-wrong details).
    #   - heuristic_baseline_scores populated (C4) and improvement_score
    #     computed from heuristic-lift (not LLM-blended-original delta).
    #   - Strategy stays role-playing (writing-appropriate), not
    #     chain-of-thought / meta-prompting (coding-side).
    #
    # Expected score: in healthy band (~7.0-7.5) because the prompt is
    # compact (≤3× budget) and the trimmed codebase keeps the optimizer
    # focused on accurate identifier citation rather than verbose
    # scaffolding.  If score drops below 6.7, the codebase-trim is
    # over-correcting.  If score goes above 7.6 with structure ≥6,
    # the full bundle is firing optimally.
    "cycle-11-b5plus-end-to-end": [
        ("Write a brief reference page for `POST /api/clusters/match` in "
         "the project's docs/ style.  Cover: (1) the request shape "
         "(`prompt`, optional `project_id`), (2) the response payload "
         "with the additive `match_level: 'family' | 'cluster'` and "
         "`cross_cluster_patterns: MetaPatternItem[]` fields, (3) the "
         "threshold cascade — family 0.55, cluster 0.45, candidate 0.65 "
         "— that determines which match level fires, and (4) when "
         "`global_injected` patterns appear in the cross-cluster list "
         "vs the topic-row injections.  Reference the canonical types "
         "in `backend/app/routers/clusters.py` and the cascade logic in "
         "`backend/app/services/taxonomy/matching.py`.  Style: present "
         "tense, two short paragraphs plus a worked example."),
    ],
    # Cycle 10 — full-bundle live validation post-C6 + B5.
    # 5 prompts designed to exercise every fix from this run:
    #   (1) writing-about-code (CHANGELOG-style) — B5 must upgrade to
    #       code_aware on the full-prompt scan after first-sentence misses.
    #   (2) backend concurrency audit — boosts backend's concurrency
    #       qualifier (race-condition vocab) and grows Race Condition
    #       Auditing's home cluster; T1.3-lite expected to accumulate.
    #   (3) frontend Svelte audit — exercises inline-backtick unwrap
    #       (`EditorGroups.svelte` is `.svelte` extension → unwrap), and
    #       both file-path identifiers should land code_aware.
    #   (4) sub-domain mechanics audit — second prompt referencing
    #       `_propose_sub_domains` semantics; pairs with cycle 8's same
    #       cluster to grow ``Sub-domain Consistency Routing Audit`` to m=3.
    #   (5) database/index audit — exercises ``RepoFileIndex`` snake_case +
    #       BLOB-packing tech vocabulary; database domain should gain a
    #       member.
    "cycle-10-comprehensive-validation": [
        # B5 — writing-about-code rescue
        ("Write a release-notes section for v0.4.7 in `docs/CHANGELOG.md` "
         "style covering Bug A repo-relevance additive gate, Bug B "
         "sub-domain consistency vocab matching, C1-C5 score calibration "
         "(asymmetric z-cap, length budget, technical conciseness blend, "
         "heuristic_baseline_scores), T1.x learning loops (Bayesian "
         "shrinkage, MMR few-shot, A4 gate, T1.3-lite counters), and the "
         "frontend observatory SSE refresh fix."),
        # Backend concurrency
        ("Audit the `_spawn_bg_task` lifecycle in `backend/app/main.py` — "
         "verify cancelled background tasks release their AsyncSession "
         "before cleanup, the WeakSet GC doesn't race with "
         "`task.add_done_callback`, and shutdown drain awaits all "
         "in-flight tasks before lifespan exit."),
        # Frontend
        ("Review the `ContextPanel.svelte` mount-gating logic in "
         "`frontend/src/lib/components/editor/EditorGroups.svelte` — "
         "confirm the panel mounts only on the prompt tab, hides during "
         "synthesis, and persists collapse state via the documented "
         "`synthesis:context_panel_open` localStorage key."),
        # Sub-domain mechanics
        ("Audit the `_propose_sub_domains` flow in "
         "`backend/app/services/taxonomy/engine.py` — verify the "
         "seed_cluster passed to `_create_domain_node` extracts populated "
         "signal_keywords, the qualifier vocabulary persists into "
         "`cluster_metadata.generated_qualifiers`, and the dissolution "
         "check uses the sub-domain's own vocab_groups for consistency "
         "matching."),
        # Database/index
        ("Trace the `RepoFileIndex.embedding` storage path in "
         "`backend/app/services/repo_index_service.py` — verify the "
         "384-dim float32 numpy arrays are packed via `.tobytes()` without "
         "serialization overhead, the embed-on-write incremental refresh "
         "skips files with unchanged `content_sha`, and curated retrieval "
         "cache invalidates correctly on source file modifications."),
    ],
    # Cycle 9 — 5 prompts spread across 5 domains.  Goals:
    #   (a) widen the score distribution so z-norm has real variance to
    #       work with (current corpus stddev≈0.6 is borderline noisy),
    #   (b) verify asymmetric C1 z-cap restores upside on confident
    #       LLM scores,
    #   (c) trigger Bug B emergence — backend gap=+0.180 makes it
    #       eligible; the next Phase-5 cadence should propose,
    #   (d) seed T1.3-lite counter increments via at least one score
    #       outside the [6.5, 7.5) no-signal band,
    #   (e) cross-domain prompts test Bug A (identifier-match) on
    #       diverse repo files (Svelte, SQL, docker-compose, auth).
    "cycle-9-5-domain-pressure": [
        # frontend
        "Audit `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.svelte` for cache-invalidation correctness — verify the heatmap re-fetches on `taxonomy_changed` SSE, not just on period selector change. Confirm the wire goes through `observatoryStore.refreshPatternDensity()` and that stale rows (e.g. dissolved sub-domains) are evicted on the next event.",
        # database
        "Optimize the `OptimizationService.get_score_distribution` SQL in `backend/app/services/optimization_service.py` — currently runs five separate per-dimension AVG/STDDEV queries against the optimizations table; collapse into one SELECT with FILTER clauses (PostgreSQL) or grouped CASE (SQLite). Preserve the dict-of-dicts shape returned to `score_blender.blend_scores`.",
        # devops
        "Audit `docker-compose.yml` for production-readiness — flag exposed dev ports (5199 vite, 8000 uvicorn), missing healthcheck blocks, mutable `latest` tags, absence of resource limits on the python services, and any volume mount that breaks immutability assumptions.",
        # security
        "Review the GitHub Device Flow OAuth implementation in `backend/app/services/github_service.py` and `backend/app/routers/github_auth.py` — verify token storage uses Fernet encryption, refresh tokens rotate on use, and 401 responses trigger refresh rather than a fresh device flow. Flag any path where a plaintext token transits a log line.",
        # writing
        "Draft a CHANGELOG entry for the v0.4.7 release. Summarize: Bug A repo-relevance additive gate, Bug B sub-domain consistency vocab-group matching, C1-C4 score calibration (asymmetric z-cap, length budget, technical conciseness blend, heuristic_baseline_scores), T1.1 Bayesian shrinkage on phase weights, T1.6 MMR few-shot diversity, T1.7 A4 gate tightened, T1.3-lite pattern usefulness counters, frontend observatory SSE refresh fix.",
    ],
    # Cycle 8 — re-runs cycle 7's prompt 2 (which was killed by an
    # in-flight uvicorn reload triggered by the structure-fix mid-cycle
    # edit) AND verifies the post-fix C2 guideline produces structured
    # output (≥1 bullet list when constraints ≥3).  Single prompt avoids
    # any cross-pipeline interference.
    "cycle-8-structure-fix-verify": [
        "Audit _reevaluate_sub_domains in engine.py — the consistency check parses domain_raw qualifiers and matches against the sub-domain's own generated_qualifiers vocab. Verify the cache-instrumentation, observability, and tracing tokens correctly route Embedding Correctness Audits and Race Condition Auditing under their parent sub-domain.",
    ],
    # Cycle 7 — full sprint bundle revalidation.  Same two prompts as
    # cycle 6 so scores diff apples-to-apples.  What this cycle exercises
    # vs cycle 6:
    #   - C1 (z-score cap |z|≤2.0): conciseness no longer floor-clamps
    #     to ~3 when LLM raw is ~7. Expected lift: ~+0.5 on overall.
    #   - C2 (length budget in analysis_summary + optimize.md): optimizer
    #     should respect the ≤3× expansion budget for concise originals.
    #     Cycle 6 prompt 1 expanded 10.2× and got dinged on conciseness;
    #     post-fix should land ≤3× and preserve original conciseness.
    #   - C3 (technical-aware conciseness blend, w_h 0.20→0.35): boosts
    #     the heuristic's structural-density signal for technical-dense
    #     prompts where LLM judges incorrectly call dense info "verbose".
    #   - C4 (heuristic_baseline_scores): new persisted field decouples
    #     improvement_score from A/B-presentation noise on the original.
    #   - T1.1 (Bayesian shrinkage on phase weights): clusters with m≥2
    #     now contribute weight signal — was m≥10 hard threshold.
    #   - T1.6 (MMR few-shot retrieval): expects DIFFERENT exemplars
    #     than cycle 6 because cycle-6's outputs are now in the corpus
    #     and the cycle-7 prompt embedding will retrieve them — MMR
    #     should preserve relevance but penalise near-duplicates.
    #   - T1.7 (A4 gate tighter, 0.5/0.2 → 0.40/0.10): fewer Haiku
    #     fallback firings on confident-heuristic technical prompts.
    #   - T1.3-lite (pattern usefulness counters): each persisted opt
    #     bumps useful_count or unused_count on its OptimizationPattern
    #     rows based on overall_score — verify in DB after the cycle.
    #   - Bug B verification: pre-cycle backend.gap=+0.150 with
    #     top_qualifier=concurrency. If Phase 5 fires during the cycle,
    #     a sub-domain proposal should emerge AND survive consistency
    #     re-evaluation (the synthetic test locked this in offline).
    "cycle-7-full-sprint-revalidation": [
        "Trace the EmbeddingService.aembed_single hot path — three sequential model.encode() calls per persist (raw, optimized, qualifier) burn 150-500ms of CPU. Audit whether engine.py:599-633 can batch them via aembed_texts without breaking the qualifier_embedding_cache short-circuit.",
        "Audit _reevaluate_sub_domains in engine.py — the consistency check parses domain_raw qualifiers and matches against the sub-domain's own generated_qualifiers vocab. Verify the cache-instrumentation, observability, and tracing tokens correctly route Embedding Correctness Audits and Race Condition Auditing under their parent sub-domain.",
    ],
    # Cycle 6 verifies the additive repo-relevance gate (Bug A) and
    # exercises the sub-domain consistency check (Bug B).
    #
    # Both prompts are deliberately narrow: short, single-class scope,
    # high identifier density.  This is the regime where the pre-fix
    # cosine-only gate suppressed codebase context (cosine ~ 0.09 on
    # 250-char prompt vs 50K-char anchor).  Post-fix, the identifier-
    # match boost (+0.10) plus domain-overlap boost (+0.01/match) should
    # lift adjusted_score over 0.15 and deliver codebase context.
    #
    # Prompt 2 is intentionally about taxonomy sub-domain mechanics so
    # the next Phase-5 maintenance cycle exercises the new consistency
    # check on a freshly-emerged or re-emerged sub-domain.
    "cycle-6-additive-gate-and-consistency": [
        "Trace the EmbeddingService.aembed_single hot path — three sequential model.encode() calls per persist (raw, optimized, qualifier) burn 150-500ms of CPU. Audit whether engine.py:599-633 can batch them via aembed_texts without breaking the qualifier_embedding_cache short-circuit.",
        "Audit _reevaluate_sub_domains in engine.py — the consistency check parses domain_raw qualifiers and matches against the sub-domain's own generated_qualifiers vocab. Verify the cache-instrumentation, observability, and tracing tokens correctly route Embedding Correctness Audits and Race Condition Auditing under their parent sub-domain.",
    ],
    # Cycle 5 post-fix clean verification (2 prompts).
    # After the post-cycle-4 fix bundle:
    #   - DomainSignalLoader.load() now exempts sub-domains and qualifier-
    #     covered domains from the "no vocabulary" warning (the spam was
    #     spurious for organic sub-domains).
    #   - _create_domain_node now receives a seed_cluster from
    #     _propose_sub_domains so signal_keywords + centroid_embedding +
    #     signal_member_count_at_generation populate at creation time.
    #   - _repair_tree_violations walks parent_id up to the top-level
    #     domain when fixing a `domain` mismatch, instead of hardcoding
    #     'general'.
    #   - _dissolve_node now re-anchors OptimizationPattern provenance
    #     onto the parent so archived sub-domain rows GC cleanly.
    # Two prompts target the live `embedding-health` sub-domain so we can
    # observe the qualifier-cache routing path firing without producing
    # false-positive warnings.
    "cycle-5-post-fix-clean": [
        "Profile the EmbeddingIndex.search latency cliff at the HNSW threshold — instrument numpy → HNSW backend swap with prometheus_client.Histogram so we can correlate cluster_count vs p95 lookup latency around HNSW_CLUSTER_THRESHOLD.",
        "Audit the DomainSignalLoader.qualifier_embedding_cache eviction policy — when keyword sets overlap heavily across sub-domains, the sorted-pipe-joined cache key admits redundant entries. Quantify hit_rate / miss_rate ratio under repeat classification load.",
    ],
    # Cycle 4 verify: post-fix qualifier-syntax verification (3 prompts).
    # Tight cycle to confirm the post-LLM enrichment + tiebreaker fixes
    # cause `domain_raw` to land with qualifier syntax (e.g.
    # ``backend: tracing``) instead of bare ``backend``.
    "cycle-4-verify-qualifier-syntax": [
        "Add OpenTelemetry tracing around ContextEnrichmentService.enrich() — wrap each profile-gated layer in its own span so the trace flame graph reveals which layer dominates request latency.",
        "Implement Prometheus metrics histograms for the EmbeddingIndex.search hot loop — separate buckets for numpy backend vs HNSW backend so we see backend-swap latency cliffs.",
        "Build a structured debug logger for TaxonomyEventLogger.log_decision — emit duration_ms + caller frame + correlated trace_id alongside the existing op/decision/path fields.",
    ],
    # Cycle 3: tracing / instrumentation sub-domain — drive a SECOND organic
    # sub-domain emergence under `backend`, alongside the existing `audit` one.
    # Strategy: every first-sentence verb is implementation/instrumentation
    # (not audit/diagnose), and every prompt mentions `tracing`,
    # `instrumentation`, `monitoring`, `metrics`, or `observability` —
    # the Haiku-generated keyword set for backend's `tracing` qualifier.
    # Identifier syntax (snake_case + Module.method) keeps `code_aware`
    # locked in via the v0.4.5 has_technical_nouns fix.
    "cycle-3-tracing-instrumentation": [
        "Implement a Prometheus metrics exporter for warm-path Phase 4 timings — instrumentation layer using prometheus_client.Histogram with p50/p95/p99 buckets keyed by phase_name.",
        "Add OpenTelemetry tracing around ContextEnrichmentService.enrich() — wrap each profile-gated layer in its own span so the trace flame graph reveals which layer dominates request latency.",
        "Build a structured debug logger for TaxonomyEventLogger.log_decision — emit duration_ms + caller frame + correlated trace_id alongside the existing op/decision/path fields.",
        "Instrument the EmbeddingIndex.search hot loop with prometheus_client histograms — separate buckets for numpy backend vs HNSW backend so we see backend-swap latency cliffs.",
        "Add tracing to the warm-path timer fire path — capture the time from taxonomy_changed event through _apply_cross_process_dirty_marks through Phase 0 entry as a continuous span chain.",
        "Implement SSE event-bus instrumentation — emit per-subscriber processing time via prometheus_client.Summary so we can correlate backpressure with subscriber count.",
        "Build a tracing wrapper around match_prompt — log the family-vs-cluster threshold cascade decision at each level so investigators see where a non-match dropped out without grepping diagnostic logs.",
        "Add execution-path instrumentation to the auto_inject_patterns flow — record the per-call (cluster_count, pattern_count, similarity_distribution) trio as a structured monitoring event.",
        "Implement metrics export for the qualifier embedding cache — instrumentation showing hit_rate, miss_rate, and eviction_count from DomainSignalLoader's qualifier_embeddings_generated counter.",
        "Build a tracing helper for the _spawn_bg_task lifecycle — emit a span at create + span at completion so weak-ref-collected tasks become visible as orphan spans in the trace graph.",
    ],
    # Cycle 2: embedding/RAG sub-domain — meta-prompt the system about its own embedding stack.
    # All 10 prompts target backend code paths in `backend/app/services/embedding_service.py` +
    # `backend/app/services/taxonomy/{embedding_index,fusion,matching,qualifier_index,
    # transformation_index,optimized_index}.py`. Tight semantic neighborhood + codebase
    # vocabulary should drive (a) backend domain growth past the sub-domain emergence
    # threshold, (b) a coherent "embedding" qualifier appearing in domain_raw, and (c) the
    # optimized output of every prompt acting as a concrete recommendation back to the user.
    "cycle-2-embedding-rag": [
        "Audit EmbeddingService.embed_single in backend/app/services/embedding_service.py — model.encode() is called without normalize_embeddings=True so every cosine_search downstream re-normalizes via np.linalg.norm. Is the redundant work hot enough to matter for match_prompt latency?",
        "Diagnose the lifespan startup in main.py — there is no warmup pass on the all-MiniLM-L6-v2 SentenceTransformer model. First request after restart pays load + dimension probe + cold encode. Quantify the latency overhead and propose a safe warmup hook.",
        "Optimize the hot-path embed sequence in TaxonomyEngine — engine.py:599-633 calls aembed_single three times sequentially for raw_prompt, optimized_prompt, qualifier_text. Could be one aembed_texts batched call. Walk the safety implications around the qualifier cache short-circuit.",
        "Trace the 5-signal composite fusion in services/taxonomy/fusion.py — PhaseWeights blends raw + optimized + transformation + pattern + qualifier with adaptive weights and L2-normalizes the result. Audit blend_embeddings for numerical stability when one signal vector is degenerate (zero norm or NaN).",
        "Review EmbeddingIndex dual-backend in services/taxonomy/embedding_index.py — numpy default flips to HNSW at HNSW_CLUSTER_THRESHOLD=1000, with fallback to numpy on HNSW failure. Audit the upsert path for label-mapping drift across the backend swap and stale tombstones.",
        "Diagnose the QualifierIndex centroid lifecycle in services/taxonomy/qualifier_index.py — per-cluster qualifier centroids drive sub-domain emergence. Find the cases where a stale centroid survives past dissolution and flag the invariant repair.",
        "Audit the TransformationIndex direction vector compute in services/taxonomy/transformation_index.py — engine.py:608 stores (optimized − raw) / ||transform|| as the direction-of-improvement vector. When the optimized_prompt is shorter than raw_prompt, magnitude collapses; verify the t_norm > 1e-9 gate handles the edge cleanly.",
        "Review match_prompt in services/taxonomy/matching.py — uses raw embeddings (no composite fusion) for cross-process consistency. Check the 50-entry LRU embedding cache TTL, family-vs-cluster threshold cascade (0.55 / 0.45 / 0.65), and the diagnostic logging on no-match.",
        "Audit the EmbeddingService.cosine_search staticmethod — embedding_service.py:140-167 is O(N) over the corpus, redundant with EmbeddingIndex's numpy/HNSW path. Either it serves a real purpose for tests/utilities or it's legacy — make the case and propose the disposition.",
        "Trace qualifier embedding caching through DomainSignalLoader.get_cached_qualifier_embedding — engine.py:625-632 checks the cache before computing aembed_single(qualifier_text). Audit the cache key composition (sorted-pipe-joined keywords), miss path, and emission of qualifier_embeddings_generated counter.",
    ],
}


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cycle = sys.argv[1]
    if cycle not in PROMPT_SETS:
        print(f"Unknown cycle '{cycle}'. Known: {list(PROMPT_SETS)}")
        sys.exit(2)

    print(f"\n=== Pre-cycle snapshot ===")
    pre = snapshot(f"{cycle}-pre")
    print_summary(pre)

    prompts = PROMPT_SETS[cycle]
    print(f"\n=== Submitting {len(prompts)} prompts ===\n")
    results: list[dict] = []
    for i, p in enumerate(prompts, 1):
        results.append(submit_prompt(p, i, len(prompts)))

    # Give warm-path 30s to settle (default debounce is 30s for taxonomy_changed)
    print("\n  Waiting 35s for warm-path debounce + Phase 5 reconciliation...")
    time.sleep(35)

    print(f"\n=== Post-cycle snapshot ===")
    post = snapshot(f"{cycle}-post")
    print_summary(post)


if __name__ == "__main__":
    main()
