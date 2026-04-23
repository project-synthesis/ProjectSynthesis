# MCP Sampling Tier Data Processing Chain

This document defines the data processing life cycle of an optimization request when routed through the **MCP Sampling Tier** (i.e. utilizing a connected IDE's LLM, such as VS Code Copilot).

The design intent of the sampling tier is to offload all heavy generative AI operations directly to the client IDE while keeping the orchestrating logic, heuristics, and taxonomy clustering local and fast. 

## At a Glance: Does the Sampling Tier use Internal LLMs? 
**No.** All synchronous generative operations in the critical path (Analyze, Optimize, Score) are delegated to the IDE's LLM via the MCP `createMessage` protocol. Zero "internal" LLM API calls (e.g. Anthropic/Claude CLI) are made during the live request latency window, with one highly specific, configurable exception (the A4 Haiku classification fallback).

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

## 2. The 3-Phase Execution (The Generative Path)
*Delegated entirely to the IDE's LLM.*

The pipeline orchestrator hands off the assembled contexts to the `MCPSamplingProvider`. This specialized provider translates standard LLM generation requests into MCP `createMessage` JSON-RPC payloads. 

VS Code intercepts these payloads, opens a background Copilot thread, and processes the text:
1. **Analyze Phase:** The server sends the `analyze.md` template. Copilot evaluates the prompt weaknesses and intent, returning structured diagnostics.
2. **Optimize Phase:** The server injects the results from the Analyze phase alongside the context into the `optimize.md` template. Copilot rewrites the prompt according to the selected strategy. 
3. **Score Phase:** The server tasks Copilot with self-evaluating the newly optimized prompt across 5 dimensions using the `scoring.md` rubric.

*At all three steps, the server sits asynchronously idle waiting for the IDE's MCP client to respond with the completed text.*

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
