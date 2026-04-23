# Heuristic Analyzer Audit Report

This document outlines the architecture, integration points, and business logic of the `HeuristicAnalyzer` across the Project Synthesis codebase. It specifically details where the heuristic analysis operates completely autonomously and where it interfaces with internal LLMs (Haiku) to fall back or enrich its context.

## 1. Core Architecture & Responsibilities

The `HeuristicAnalyzer` acts as a zero-LLM orchestrator that processes raw user prompts at request time. It evaluates the prompt to extract classification data essential for routing and context enrichment.

It extracts:
- **`task_type`**: (e.g., `coding`, `writing`, `analysis`). It relies heavily on compound keyword algorithms and technical verb+noun disambiguation.
- **`domain`**: (e.g., `backend`, `frontend`, `devops`). Guided by organic signals and the taxonomy engine.
- **`intent_label`**: A 3-6 word label defining the prompt's intent.
- **`strengths` / `weaknesses`**: Structural metrics like missing constraints or verb ambiguity.
- **`recommended_strategy`**: Suggests the optimal optimization strategy based on the extracted classifications.

By operating mostly as a heuristic rules engine, the analyzer ensures extremely low-latency evaluation without consuming API quotas or incurring rate limits on the critical path.

## 2. Integration Points across the Codebase

The `HeuristicAnalyzer` is tightly woven into the data pipeline. The primary integrations are:

### A. Context Enrichment (`context_enrichment.py`)
For **every single optimization request** regardless of the tier (Internal, Sampling, or Passthrough), the `ContextEnrichmentService` invokes the `HeuristicAnalyzer` as the very first step. 
The resulting `HeuristicAnalysis` dataclass populates the `enrichment_meta_dict["heuristic_analysis"]`. This data directly controls:
- **Profile Routing**: (e.g., whether to use a `code_aware` profile or a `knowledge_work` profile).
- **Curated Retrieval**: Uses the heuristics to determine if it should query the local codebase vectors.
- **Strategy Injection**: Matches the prompt's domain and intent against historical taxonomic data.

### B. Routing and Triage (`main.py` & `optimize.py` & `health.py`)
The `HeuristicAnalysis` structures are used to record divergence metrics (the gap between heuristic rules and LLM scoring) via `ScoreBlender`. Additionally, `synthesis_analyze` directly returns these heuristic rules explicitly to inform the user about their prompt structure before invoking operations.

### C. Text Cleanup / Pre-processing (`app/utils/text_cleanup.py`)
Minor utility calls to `HeuristicAnalyzer._extract_meaningful_words` to tokenize inputs structurally based on built-in arrays without heavy NLTK pipelines.

## 3. The Haiku / LLM Fallback Chain (Layer A4)

While the `HeuristicAnalyzer` is predominantly a local, rules-based engine, business logic dictates that approximately 15-20% of prompts cannot be neatly classified by regex or rigid disambiguation logic. This triggered the creation of the **A4 Classification Fallback**.

### When is Haiku injected?
During `_analyze_inner()`, the system calculates a `task_confidence` score based on keyword densities.
If:
1. `disambiguation_applied` is FALSE (i.e. no clear verb+noun technical matches).
2. The `task_confidence` falls below the threshold (`_LLM_CLASSIFICATION_CONFIDENCE_GATE`).
3. The preference `enable_llm_classification_fallback` is TRUE.

Then the system triggers `classify_with_llm()`, an orchestrated call to the local internal provider configured for the background environment—**which defaults to Haiku**. 

### Why Haiku?
Haiku is optimized for ultra-low latency categorization. The orchestrator uses Haiku to quickly review the prompt and return a structured classification string containing a forced `task_type` and `domain`. Because this happens before optimization begins, relying on Haiku prevents high latency penalties from propagating into the core prompt-engineering pipeline.

## 4. Sub-Domain Taxonomy Operations (Background Haiku Usage)

Outside the request lifecycle, the `HeuristicAnalyzer` shares deep integration overlaps with the **Taxonomy/Clustering layer** where Haiku plays a dominant role.

- **Vocabulary Generation (`labeling.py` & `domain_detector.py`)**: During Phase 5 of the Warm Path background maintenance cycles, Haiku generates new taxonomical labels and vocabulary based on growing cluster centroids. 
- **`DomainSignalLoader`**: Organically digests the Haiku labels into heuristic signals which the `HeuristicAnalyzer` then relies upon in real-time. This essentially creates a self-healing taxonomy loop: Haiku clusters and defines terms asynchronously in the backend -> The Analyzer uses those terms to route live traffic instantly via regex/signals.
- **Pattern Extraction (`family_ops.py`)**: Haiku extracts meta-patterns from completed pipelines to seed future heuristic associations.
- **Codebase Exploration (`codebase_explorer.py`)**: Background generation of architectural `.md` summaries.

## Summary
The `HeuristicAnalyzer` effectively functions as the primary **CPU-bound gateway**, gating the expensive, token-heavy LLM calls. It only yields its classification responsibilities to Haiku precisely when inputs violate structural assumptions (the A4 hook). This dual system ensures semantic correctness while maintaining optimal runtime velocity.
