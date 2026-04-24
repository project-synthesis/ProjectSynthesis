# MCP Sampling Tier Data Processing Chain

_Last reviewed: 2026-04-24 (reflects Hybrid Phase Routing shipped in v0.4.2)._

This document defines the data processing life cycle of an optimization request when routed through the **MCP Sampling Tier** (i.e. utilizing a connected IDE's LLM, such as VS Code Copilot).

The design intent of the sampling tier is to offload the heaviest generative AI operations directly to the client IDE while keeping the orchestrating logic, heuristics, and taxonomy clustering local and fast.

## At a Glance: Does the Sampling Tier use Internal LLMs?

**Yes — selectively, via Hybrid Phase Routing.** As of v0.4.2, when the sampling tier is selected and an internal provider (Claude CLI or Anthropic API) is also available, the pipeline runs the **fast phases (analyze, score, suggest) on the internal provider** and routes only the **optimize phase** through the IDE LLM. The decision is encoded in `RoutingDecision.providers_by_phase` from `services/routing.py`. If no internal provider is available (force_sampling on a raw environment), every phase falls back to sampling and `providers_by_phase={analyze: sampling, score: sampling, suggest: sampling, optimize: sampling}`.

Why: analyze/score/suggest produce small structured JSON that doesn't benefit from the user's IDE model but previously cost one full IDE round-trip each (5-12 s on Sonnet 4.6 sampling), blocking perceived latency. Rewriting a prompt does benefit from the user's chosen model, so optimize stays on sampling.

The A4 Haiku classification fallback still applies to the heuristic pre-processing stage and is gated by the `enable_llm_classification_fallback` preference — independent of tier.

Below is the chronological step-by-step data processing chain.

---

## 1. Context Enrichment (Pre-Processing)
*Executed entirely locally on the server.*

When the `synthesis_optimize` workflow is triggered, the `ContextEnrichmentService` builds a comprehensive state snapshot around the raw prompt.
- **Heuristic Analysis:** The `HeuristicAnalyzer` uses regex, verb-noun disambiguation arrays, compound keyword matching, and taxonomy domain signals to determine the `intent`, `task_type`, and `domain`. 
    - *A4 Classification Fallback:* If a prompt is incredibly vague and heuristics fail, the system CAN fall back to a small Haiku call to categorize it. This is strictly gated by the `enable_llm_classification_fallback` user preference and can be disabled to enforce a true 0-internal-call pipeline.
- **Codebase Discovery:** The pipeline retrieves the previously cached `explore_synthesis` from the SQLite database.
- **Repo Relevance Gate (B0):** The `compute_repo_relevance` routine runs a cosine similarity check to ensure the codebase context maps structurally to the prompt. This utilizes the local `sentence-transformers` embedding model. *No LLM call.*
- **Divergence Detection:** Discrepancies between the prompt's tech stack and the codebase's tech stack are flagged locally using string overlap and heuristic checks.
- **Strategy & Pattern Injection:** Historical successful patterns and the ideal PromptForge strategy are selected by comparing the prompt's local embedding against the Taxonomy Engine (`PromptCluster` centroids). *No LLM call.*

## 2. The Generative Path (Hybrid Phase Routing)
*Internal provider handles fast phases; IDE LLM handles optimize.*

Under Hybrid Phase Routing (v0.4.2), the pipeline orchestrator dispatches each phase according to `RoutingDecision.providers_by_phase`:

| Phase | Provider (sampling tier, internal available) | Provider (sampling tier, no internal) | Template |
|-------|----------------------------------------------|---------------------------------------|----------|
| Analyze | Internal (Claude CLI or Anthropic API, typically Haiku) | IDE LLM via `MCPSamplingProvider` | `analyze.md` |
| Optimize | IDE LLM via `MCPSamplingProvider` | IDE LLM via `MCPSamplingProvider` | `optimize.md` |
| Score | Internal (Haiku by default) | IDE LLM via `MCPSamplingProvider` | `scoring.md` |
| Suggest (post-score) | Internal | IDE LLM via `MCPSamplingProvider` | `suggest.md` |

`MCPSamplingProvider` is a first-class `LLMProvider` (see `backend/app/providers/sampling.py`) that translates standard generation requests into MCP `createMessage` JSON-RPC payloads. MCP transport timeouts and errors map to the `ProviderError` hierarchy so Tenacity exponential backoff retries apply uniformly with the internal providers.

For the optimize phase (and any phase falling back to sampling), the server sits asynchronously idle waiting for the IDE's MCP client to respond. VS Code intercepts the `createMessage` request, opens a background Copilot thread, and returns the completion text.

**Structured output fallback** (sampling-only): if the IDE returns tool-calling errors, the server inlines the JSON schema into the prompt and falls back to text parsing (direct JSON → code block → brace-depth). Analyze-only has a final keyword-classification fallback (`_build_analysis_from_text()`).

## 3. Post-Processing & Hybrid Scoring
*Executed entirely locally on the server.*

Once Copilot returns the results:
- **Heuristic Blending:** The `score_blender.py` service executes model-independent heuristic scoring across the output (evaluating token length, readability, markdown structures, verb actionability). It merges Copilot's self-assigned scores with the local heuristic scores using z-score normalization to detect divergence and establish a true `overall_score`. *No LLM call.*
- **Database Persistence:** The final artifact is written to the SQLite `data/synthesis.db` as an `Optimization`.

## 4. Background Taxonomy Maintenance (Asynchronous)
*Decoupled from the request chain.*

While the optimization request is fully resolved and returned to the user, background maintenance cycles manage the global taxonomy map.
- The new optimization receives a local semantic embedding (via `SentenceTransformer`) and is merged into the hot-path `PromptCluster` index.
- **Sub-domain Vocabulary & Extraction:** During background Phase 5 cycles, if clusters grow large enough to warrant new domains, a background system (`DomainSignalLoader`) runs batch operations to extract taxonomic signatures. This background housekeeping utilizes the `internal` provider (e.g. Haiku) to ensure high-quality linguistic clustering, but it **never impacts the latency or execution of an active sampling user request**.
