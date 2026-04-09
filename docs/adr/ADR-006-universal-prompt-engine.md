# ADR-006: Universal Prompt Engine — Domain-Agnostic Architecture

**Status:** Accepted
**Date:** 2026-04-08
**Authors:** Human + Claude Opus 4.6

## Context

### The Product Identity Question

Project Synthesis has two identities:

1. **The engine** — a self-organizing taxonomy that clusters prompts, discovers patterns, and injects proven techniques. This is domain-agnostic. A marketing team's prompts would organically discover "copywriting", "brand-voice", "campaign" domains the same way developer prompts discovered "backend", "frontend", "devops".

2. **The scaffolding** — GitHub integration, codebase scanning, IDE MCP server, developer-specific domain seeds, code-focused seed agents. This assumes the user is a developer.

The engine is the value. The scaffolding is the distribution channel. The question is whether the architecture should constrain itself to developers or remain universal.

### Current State Audit

An audit of the codebase reveals the engine is already universal, but the scaffolding introduces developer bias at 10 specific points:

**Hard blocks (features unavailable to non-developers):**

| Item | What | Impact |
|------|------|--------|
| Seed agents | 5/5 are developer-focused (coding, testing, architecture, analysis, docs) | Zero agents for marketing, legal, business, content creation |
| GitHub integration | Sole external integration; required for explore phase | Non-developers cannot use context enrichment from external sources |
| Codebase scanning | Scans for Python/Node manifests, code files, `.cursorrules` | No workspace intelligence for non-code projects |
| Domain keyword seeds | All keywords are tech-focused (API, React, Docker, SQL) | Non-developer prompts always classified as "general" |
| Workspace files | Looks for `CLAUDE.md`, `.cursorrules` — developer tooling files | No guidance file discovery for non-technical workspaces |

**Soft biases (features work but with reduced quality):**

| Item | What | Impact |
|------|------|--------|
| Pre-seeded domains | 6/8 are technical; "general" has zero keywords | Non-developer domains never organically emerge without traffic |
| Analyzer prompt | 6 technical domain rules, 1 SaaS example, rest → "general" | No explicit decision rules for marketing, legal, education, etc. |
| Heuristic analyzer | Weakness detection for code/data/system; minimal for writing | Business weaknesses (missing market context, unclear audience) never flagged |
| Seed strategy mapping | `coding → structured-output`, `writing → role-playing` | Reasonable defaults but no domain-specific refinement for non-dev work |
| README language | Claims "prompt optimization tool" but 70% of features/examples are developer-centric | Sets wrong expectation for non-developer users |

**Already universal (no changes needed):**

| Item | What |
|------|------|
| Task type classification | 7 types including `writing`, `analysis`, `creative`, `general` |
| Strategy files | All 6 strategies are domain-agnostic |
| Taxonomy engine | Organic domain discovery, pattern extraction, cross-project sharing |
| EmbeddingIndex | Clusters by semantic similarity, not by hardcoded domain |
| GlobalPattern tier | Promotes patterns across projects regardless of domain |
| Scoring system | 5-dimension evaluation works on any prompt type |
| Refinement loop | Iterative improvement is domain-agnostic |

### The Core Insight

The taxonomy engine's organic domain discovery IS the universal mechanism. When a marketing team uses Project Synthesis:
- Their prompts cluster by semantic similarity (not by pre-coded rules)
- Domains like "copywriting", "brand-voice", "email-campaign" emerge organically
- MetaPatterns extract reusable techniques ("always specify target audience and tone")
- Cross-project GlobalPatterns share universal techniques across teams

This already works. The only thing preventing it is the scaffolding — specifically the 5 hard blocks that make the non-developer experience empty.

## Decision

### Principle: The engine is universal. The scaffolding is extensible.

**The taxonomy engine, scoring system, pattern extraction, and optimization pipeline must NEVER be narrowed to developer-only use.** No feature should assume prompts are about code. No classification should hardcode developer domains. No pattern extraction should privilege technical signals over other domains.

**The developer scaffolding is the FIRST vertical, not the ONLY one.** GitHub integration, codebase scanning, and IDE MCP are the developer distribution channel. Future verticals (marketing, legal, education, etc.) would add their own scaffolding without modifying the engine.

### Architectural Constraints

1. **No hardcoded domain assumptions in the engine layer.** Domain classification must flow through organic discovery (`_propose_domains()` in warm path Phase 5), not through hardcoded rules. The pre-seeded developer domains are bootstrapping data, not architectural constraints.

2. **Task type classification stays universal.** The 7 task types (`coding`, `writing`, `analysis`, `creative`, `data`, `system`, `general`) cover any domain. If a new type is needed (e.g., `legal`, `marketing`), it's added to the Literal enum — the engine adapts.

3. **Seed agents are the extensibility mechanism for verticals.** The `prompts/seed-agents/*.md` hot-reload system is the correct pattern. Adding marketing seed agents is a content addition, not a code change. Each vertical ships its own seed agents.

4. **External integrations should be pluggable, not GitHub-only.** The explore phase should support multiple context sources: GitHub repos (developers), Google Drive (business teams), Notion (product teams), local file systems (anyone). The `ContextEnrichmentService` abstraction already supports this — only the GitHub implementation exists today.

5. **Heuristic signals should be domain-configurable.** The `HeuristicAnalyzer`'s weakness detection signals should be loadable from domain metadata (similar to how `DomainSignalLoader` loads keywords). A marketing domain's metadata would include weaknesses like "missing target audience definition" or "no call-to-action specified". A legal domain would include "missing jurisdiction" or "no precedent cited".

6. **Pattern extraction and injection are already universal.** `extract_patterns.md` and `pattern_injection.py` operate on semantic similarity, not domain labels. No changes needed — this is the correct architecture.

7. **The analyzer prompt's domain rules should be examples, not constraints.** The current analyzer prompt lists 7 explicit domain decision rules (backend, frontend, database, devops, security, fullstack, data). These should be framed as examples of organic domains, with guidance to classify ANY subject area — not just technical ones. The instruction "use a descriptive domain name (marketing, finance, education, legal, design)" already exists but is buried as a fallback.

### What This Means in Practice

**For the engine layer (no changes needed):**
- Taxonomy clustering, pattern extraction, GlobalPattern promotion, adaptive scheduling, EmbeddingIndex — all domain-agnostic by design
- Organic domain discovery fires when coherent sub-populations emerge under "general" — works for any prompt type
- Cross-project pattern sharing evaluates semantic similarity, not domain labels

**For the scaffolding layer (extensible, not modified):**

The following are CORRECT as developer-first implementations. They should NOT be generalized — they should be supplemented with additional verticals when demand exists:

| Component | Developer vertical (exists) | Future vertical (example) |
|-----------|---------------------------|--------------------------|
| Seed agents | `coding-implementation.md`, `testing-quality.md`, etc. | `marketing-copywriting.md`, `legal-contract-review.md` |
| External integration | GitHub repos → codebase context | Google Drive → document context, Notion → knowledge base context |
| Workspace scanning | Code manifests, `.cursorrules` | Brand guidelines, style guides, tone-of-voice docs |
| Domain keyword seeds | backend, frontend, devops, etc. | copywriting, campaign, brand-voice, audience-research |
| Heuristic weaknesses | "no language specified", "missing test criteria" | "no target audience", "missing call-to-action", "unclear brand voice" |

**For documentation and positioning:**
- README should describe the product as "AI-powered prompt optimization" (not "for developers")
- Developer features should be positioned as the first vertical, with language acknowledging the engine is universal
- The architecture overview should explain the engine/scaffolding distinction

### The Engine Is Already Universal — No Code Changes Needed

The engine layer requires zero modifications to support non-developer use cases. Every component that processes, clusters, scores, and improves prompts is domain-agnostic today:

- **Taxonomy clustering** groups by semantic similarity, not by domain label
- **Pattern extraction** finds reusable techniques in any prompt type
- **GlobalPattern promotion** shares proven techniques across projects regardless of subject matter
- **Scoring** evaluates clarity, specificity, structure, faithfulness, and conciseness — dimensions that apply to any prompt
- **Refinement** iterates on any prompt with version tracking and rollback
- **Adaptive scheduling** optimizes warm-path performance independent of what the prompts are about

When a non-developer vertical is needed, the extension points are all content additions — no code changes, no migrations, no architectural modifications.

### How to Add a New Vertical (Content-Only Playbook)

#### 1. Seed Agents — drop `.md` files, instant availability

Create new files in `prompts/seed-agents/` with YAML frontmatter. The hot-reload system picks them up on the next request — no restart, no deployment, no code change.

**Example: marketing vertical**
```
prompts/seed-agents/
  marketing-copywriting.md      # sales copy, landing pages, email campaigns
  marketing-brand-voice.md      # brand guidelines, tone consistency, messaging frameworks
  marketing-audience-research.md # personas, pain points, competitive positioning
```

Each file defines: `name`, `task_types` (from the 7 universal types), `phase_context` (what the agent focuses on), `prompts_per_run`, and `enabled`. The existing batch seeding infrastructure (`POST /api/seed`, `synthesis_seed` MCP tool) works immediately with new agents.

**Example: legal vertical**
```
prompts/seed-agents/
  legal-contract-drafting.md    # clause writing, term definitions, liability scoping
  legal-compliance-review.md    # regulation analysis, policy evaluation, risk assessment
  legal-brief-writing.md        # case summaries, argument structure, citation formatting
```

#### 2. Domain Keyword Seeds — bootstrap classification for new subject areas

Add domain keyword seeds via the existing `DomainSignalLoader` pattern. Currently, keywords are stored in `cluster_metadata` on domain `PromptCluster` nodes. Bootstrapping a new vertical's domains uses the same Alembic migration pattern as the original developer domains.

**Example migration for marketing domains:**
```python
# New domains with keyword signals
marketing_domains = [
    {"label": "copywriting", "keywords": ["copy", "headline", "CTA", "landing page", "conversion", "persuasion", "benefit-driven"]},
    {"label": "brand-voice", "keywords": ["tone", "brand", "voice", "persona", "messaging", "guidelines", "style guide"]},
    {"label": "audience-research", "keywords": ["persona", "demographic", "pain point", "customer journey", "segmentation", "ICP"]},
    {"label": "campaign", "keywords": ["campaign", "funnel", "A/B test", "email sequence", "drip", "nurture", "retention"]},
]
```

Once seeded, the organic domain discovery system takes over — new domains emerge from user behavior without further manual intervention. The seeds just accelerate the cold-start.

#### 3. Heuristic Weakness Signals — domain-specific quality detection

The `HeuristicAnalyzer`'s weakness detection is signal-driven. Adding new signals for a vertical means extending the signal dictionaries. No structural changes — just new entries.

**Example: marketing-specific weakness signals**
```python
# In heuristic_analyzer.py or loaded from domain metadata
_MARKETING_WEAKNESS_SIGNALS = {
    "missing_target_audience": ["who is this for", "target audience", "persona", "demographic"],
    "no_call_to_action": ["CTA", "call to action", "next step", "click", "sign up", "buy"],
    "unclear_value_proposition": ["benefit", "value prop", "why should", "what's in it for"],
    "missing_brand_voice": ["tone", "voice", "brand", "personality", "style"],
    "no_competitive_context": ["competitor", "alternative", "differentiate", "unique"],
}
```

The future-state architecture loads these from domain node metadata (the same `cluster_metadata` JSON that already stores keyword signals), making weakness detection fully data-driven without code changes.

#### 4. Context Providers — pluggable external integrations

The `ContextEnrichmentService` abstraction already supports multiple context sources. Currently, only GitHub is implemented. Adding a new provider is a single-service implementation behind the existing interface.

**Potential providers by vertical:**

| Vertical | Context provider | What it brings |
|----------|-----------------|----------------|
| Marketing | Google Drive | Brand guidelines, campaign briefs, past copy |
| Legal | Document management (Clio, NetDocuments) | Precedent library, clause databases, jurisdiction rules |
| Education | LMS (Canvas, Moodle) | Curriculum standards, learning objectives, rubrics |
| Product | Notion / Confluence | PRDs, user stories, feature specs |
| Any | Local filesystem | Any workspace directory (already partially supported) |

Each provider implements the same interface: given a project context, return relevant documents ranked by semantic similarity. The explore phase works identically regardless of source — it synthesizes context from whatever documents the provider returns.

#### 5. Analyzer Prompt Expansion — broaden domain decision rules

The current `prompts/analyze.md` lists explicit decision rules for 7 technical domains. Adding new verticals means expanding these rules with examples for non-technical domains. Since prompts are hot-reloaded from disk, this is a text edit — no code change, no deployment.

**Example expansion in analyze.md:**
```
Domain decision rules (examples — discover the domain from the prompt's subject area):
...existing developer rules...
- Copy, headlines, CTAs, landing pages, email sequences, conversion → `copywriting`
- Brand voice, tone, messaging, style guidelines → `brand-voice`
- Contracts, clauses, liability, compliance, jurisdiction → `legal`
- Lesson plans, curriculum, learning objectives, assessment → `education`
- Product requirements, user stories, feature specs, roadmap → `product`
- Otherwise → use a descriptive domain name or `general`
```

### Vertical Rollout Strategy

**Developers (now):** Fully built. GitHub integration, 5 seed agents, 8 domain seeds, code-specific heuristics. This is the current product.

**Next vertical (on demand):** When a non-developer audience is identified with sufficient demand, execute the 5-step content playbook above. Estimated effort: 1-2 days of content creation (seed agents, domain keywords, analyzer examples) — zero engineering time on the engine.

**Organic expansion:** Even without explicit vertical support, non-developer users benefit from the universal engine. Their prompts cluster, patterns emerge, and quality improves over time. The explicit vertical support (seed agents, keywords, weakness signals) just accelerates the cold-start.

**Cross-vertical pattern sharing:** This is the ADR-005 GlobalPattern system's key value proposition. A technique like "always specify the target audience and desired outcome" might be discovered in marketing prompts, promoted to a GlobalPattern, and then injected into a developer's user-facing documentation prompt. The engine facilitates cross-domain knowledge transfer that no single-vertical tool can provide.

## Consequences

### Positive

- The engine scales to any domain without code changes
- New verticals are content additions (seed agents, domain keywords), not architectural changes
- Cross-domain pattern sharing works universally — a technique discovered in marketing ("always specify target audience") can benefit a developer writing user-facing documentation
- The product can expand beyond developers without breaking existing functionality
- Clear separation of engine (universal) from scaffolding (vertical-specific) prevents future narrowing

### Negative

- The developer scaffolding may create a perception that the tool is developer-only, even though the engine is universal
- Non-developer users who discover the tool today will have a degraded experience (no seed agents, no context enrichment, "general" domain for everything)
- Adding new verticals requires content creation effort (seed agents, domain keywords, weakness signals) even though no code changes are needed

### Risks

- **Feature drift toward developer-only:** Without this ADR, new features might hardcode developer assumptions. Example: a "code review" feature that only works on GitHub PRs, when the engine could support "content review" for any text. Mitigation: this ADR establishes the principle that engine-layer features must be universal.
- **Quality gap for non-developers:** Even though the engine works, the quality of optimization for non-developer prompts is lower because few-shot examples, domain keywords, and weakness detection are all developer-focused. Mitigation: organic domain discovery and pattern extraction will improve quality over time as non-developer prompts accumulate.
- **Complexity of multi-vertical support:** Supporting multiple context providers (GitHub, Google Drive, Notion) adds integration complexity. Mitigation: Phase 3 is on-demand, triggered by actual user need.

## References

- Taxonomy engine: `backend/app/services/taxonomy/` (domain-agnostic by design)
- Organic domain discovery: `engine.py` → `_propose_domains()` (fires for any coherent sub-population)
- Seed agents: `prompts/seed-agents/*.md` (hot-reloaded, extensible)
- Domain signal loader: `backend/app/services/domain_signal_loader.py` (keyword-driven, configurable)
- Context enrichment: `backend/app/services/context_enrichment.py` (abstraction layer)
- Heuristic analyzer: `backend/app/services/heuristic_analyzer.py` (signal-driven, extensible)
- ADR-005: Taxonomy scaling architecture (multi-project isolation supports multi-vertical)
