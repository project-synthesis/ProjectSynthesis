# Landing Page Consolidation — Design Spec

**Date:** 2026-03-17
**Status:** Approved
**Scope:** Consolidate 15 content subpages into a single-scroll landing page with scroll-driven animations. Keep 4 legal/info pages. Remove 11 subpages.

---

## Overview

The current landing page has 6 static sections plus 15 content subpages linked from a 4-column footer. This redesign consolidates the valuable content into one scroll-driven narrative and removes redundant/fictional pages. The result: a single-scroll experience that tells the complete product story with modern motion, plus 4 standalone legal pages.

**Narrative arc:** What it does → How it works → Prove it → Use it anywhere → Trust it → Get started.

---

## Pages After Consolidation

**Main scroll (`/`):** Hero → Pipeline → Live Example → Works Everywhere → Get Started + Trust → Footer

**Kept as separate `/[slug]` routes (4):** privacy, terms, security, changelog

**Removed (11):** pipeline, scoring, refinement, integrations, documentation, api-reference, mcp-server, about, blog, careers, contact

---

## Section 1: Hero

**Headline:** "PROMPTS IN. BETTER PROMPTS OUT." (Syne uppercase, gradient text on second line)

**Subheading:** "AI-powered prompt optimization pipeline. Analyze, rewrite, and score — with or without an API key. Self-hosted. Open source. Measurably better."

**Product mockup:** Animated pipeline visualization. On page load, the 3 phases play sequentially (500ms stagger):
1. ANALYZE phase — cyan badge, task type + strategy text types in
2. OPTIMIZE phase — purple badge, +structure +constraints text appears
3. SCORE phase — green badge, overall score counter animates to 8.4, dimension bars fill

The mockup uses CSS `@keyframes` with `animation-delay` for the sequential reveal. No scroll dependency — this fires on page load.

**CTAs:** "View on GitHub" (solid cyan, GitHub icon) + "See It Work" (ghost, scrolls to `#example`)

---

## Section 2: Pipeline Deep-Dive

**Section heading:** "THREE PHASES. ZERO GUESSWORK."

**Layout:** Scroll-pinned sticky container. As the user scrolls through ~3 viewport-heights of scroll distance, one central visualization transforms through 3 states. Content panels appear beside the visualization as each phase activates.

### Scroll-driven mechanism

The section wrapper has `height: 300vh`. Inside it, a sticky container (`position: sticky; top: 0; height: 100vh`) holds the visualization. Three content panels are positioned at 0vh, 100vh, and 200vh offset. As each panel enters the viewport, the visualization transforms via CSS `animation-timeline: scroll()`.

**Fallback (no scroll-timeline support):** Three stacked panels, each visible, no sticky behavior. Functionally identical content, just sequential instead of animated.

**Mobile (< 768px):** Always use the stacked fallback regardless of scroll-timeline support. A 300vh sticky section on a small screen creates excessive scroll distance. The `@media (max-width: 768px)` query forces the non-sticky, stacked layout with `data-reveal` fade-up on each phase panel.

### Phase content

**Phase 1 — ANALYZE (scroll 0-33%):**
- Visualization: raw prompt text visible, analysis overlay appears
- Color: cyan
- Text panel: "Classifies task type. Detects weaknesses. Selects from six optimization strategies. Confidence gate at 0.7 triggers automatic fallback."
- Visual cues: task type badge animates in, weakness callouts appear as small tags

**Phase 2 — OPTIMIZE (scroll 33-66%):**
- Visualization: raw prompt fades left, optimized prompt builds right with visible structure (## headers)
- Color: cyan → purple transition
- Text panel: "Rewrites using the selected strategy. Adds structure, constraints, and specificity. Injects codebase context when a repo is linked. Every word earns its place."
- Visual cues: strategy badge, change summary tags

**Phase 3 — SCORE (scroll 66-100%):**
- Visualization: 5 dimension bars animate to values, delta badges appear, overall score lands
- Color: purple → green
- Text panel: "Blind A/B evaluation. LLM scores blended with model-independent heuristics. Randomized presentation order prevents position bias. Z-score normalized when history exists."
- Visual cues: score bars fill, delta counters count up

---

## Section 3: Live Example

**Section heading:** "BEFORE AND AFTER."

**Showcase prompt:** "Build a REST API for a todo app"

This is a real-world prompt that every developer has written. It's vague enough to show dramatic improvement but specific enough to be relatable.

### Before panel (dimmed, left or top)

```
Build a REST API for a todo app
```

Displayed in a mockup container with `bg-input`, `text-dim`, `font-mono`. Minimal — the emptiness is the point.

**Analyzer output overlay (appears on scroll):**
- Task type: `coding`
- Weaknesses: "No language specified", "No endpoint signatures", "No error handling", "No response format", "No auth requirements", "No validation rules"
- Strategy selected: `structured-output`
- Confidence: 0.92

### After panel (bright, right or bottom)

The optimized prompt rendered with visible markdown structure:

```
## Task
Build a REST API for a todo application.

## Endpoints
- POST /todos — Create a todo. Body: { title: string, completed?: boolean }
- GET /todos — List all todos. Query: ?completed=true|false for filtering
- GET /todos/:id — Get single todo. Return 404 if not found
- PATCH /todos/:id — Partial update. Accept any subset of { title, completed }
- DELETE /todos/:id — Delete. Return 204 on success, 404 if not found

## Constraints
- Language: Python 3.12 with FastAPI
- Validation: Pydantic models for all request/response bodies
- Error handling: Return { detail: string } with appropriate HTTP status codes
- ID generation: UUID v4
- Storage: In-memory dict (no database required)

## Output
- Complete, runnable Python file
- Include type hints on all functions
- Include docstrings on each endpoint
```

Displayed in a mockup container with `bg-card`, `text-primary`, visible `##` headers in `neon-cyan`.

### Score comparison

Below both panels, a 5-dimension score comparison with animated bars:

| Dimension | Before | After | Delta |
|-----------|--------|-------|-------|
| Clarity | 3.2 | 8.1 | +4.9 |
| Specificity | 2.0 | 8.8 | +6.8 |
| Structure | 2.2 | 9.0 | +6.8 |
| Faithfulness | 5.0 | 8.4 | +3.4 |
| Conciseness | 8.0 | 7.2 | -0.8 |

- "Before" bars: short, `text-dim` color
- "After" bars: long, dimension-specific neon colors (cyan/purple/green/yellow/pink)
- Delta badges: green for positive, red for negative (-0.8 conciseness is honest — more words because more structure was needed)
- Bars animate width on scroll via `animation-timeline: view()`
- Delta numbers count up via `@property --num`

**Below the scores:** "Five dimensions. Hybrid LLM + heuristic scoring. Blind A/B evaluation with randomized presentation order."

---

## Section 4: Works Everywhere

**Section heading:** "NO VENDOR LOCK-IN. NO API KEY REQUIRED."

Three value tiers presented as a compact horizontal strip. No code snippets — high-level value language only.

### Tier 1 — Zero Config
**Icon:** Power/plug symbol in cyan
**Heading:** "Zero Config"
**Text:** "Works with Claude CLI out of the box. Max subscription means zero marginal cost per optimization. No API key, no billing, no setup."

### Tier 2 — MCP Passthrough
**Icon:** Circuit/nodes symbol in purple
**Heading:** "Your IDE, Your LLM"
**Text:** "Drop the pipeline into your editor. Your IDE's model does the optimization — Synthesis orchestrates the phases, scores the result, tracks the history."

**Logo strip below Tier 2:** Horizontally scrolling infinite loop of MCP-compatible IDE logos. Monochrome at `text-dim` opacity. Each logo brightens to full white briefly as it passes center.

Logos (in order): Claude Code, Cursor, Windsurf, VS Code, Zed, JetBrains

**Logo assets:** Inline SVG elements hardcoded in the component. Simple wordmarks or simplified icons — NOT official brand logos (avoid trademark issues). Each is a `<span>` with the IDE name in `font-mono` at `text-dim` opacity. No external CDN, no image imports, no icon library dependency.

Implementation: CSS `@keyframes` infinite scroll on a duplicated logo strip. `animation: scroll 30s linear infinite`. No JS.

### Tier 3 — Codebase-Aware
**Icon:** Git-branch symbol in green
**Heading:** "Codebase-Aware Optimization"
**Text:** "Link a GitHub repo and the optimizer learns your conventions. Function signatures, error handling patterns, naming standards, architecture decisions — optimized prompts reference YOUR code, not generic examples."

---

## Section 5: Get Started + Trust

**Background:** `bg-secondary` with 1px gradient top border (brand gradient, cyan → purple)

### Mission line
One sentence, centered, `text-secondary`:
"Built by engineers who got tired of vague prompts. Apache 2.0 licensed. No telemetry. No cloud dependency. Your prompts never leave your infrastructure."

### Trust badges
Horizontal row of 4 compact badges, centered. Each links to its legal page.

| Badge | Link | Icon concept |
|-------|------|------|
| Encrypted at rest | `/security` | Lock |
| Zero telemetry | `/privacy` | Eye-off |
| Apache 2.0 | `/terms` | Scale |
| Self-hosted | `/privacy` | Server |

Each badge: `border-subtle`, `text-dim`, `font-mono` 10px. Hover: `border-accent`, text brightens. Simple inline SVG icons.

### CTA
Gradient text heading: "STOP GUESSING. START MEASURING."
Sub: "Every prompt scored. Every improvement tracked. Every iteration versioned."
Button: "View on GitHub" (solid cyan, GitHub icon)

---

## Section 6: Footer

**Reduced to 2 columns + meta row:**

| Product | Legal |
|---------|-------|
| Pipeline (anchor `#pipeline`) | Privacy (`/privacy`) |
| Live Example (anchor `#example`) | Terms (`/terms`) |
| Integrations (anchor `#integrations`) | Security (`/security`) |
| Changelog (`/changelog`) | |

**Meta row:** Copyright + `APP_VERSION` (from `$lib/version.ts`) + GitHub link

---

## Animation System

All animations use CSS-native features. No JS animation libraries.

### Page load (Hero)
- Hero headline: fade-in + slide-up, 500ms, 200ms delay
- Subheading: fade-in, 300ms, 400ms delay
- CTAs: fade-in, 300ms, 600ms delay
- Mockup: sequential phase animation (ANALYZE 800ms delay, OPTIMIZE 1300ms, SCORE 1800ms)

### Scroll-pinned pipeline (Section 2)
```css
.pipeline-section {
  height: 300vh;
  position: relative;
}

.pipeline-sticky {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  align-items: center;
}
```

Phase transitions driven by `animation-timeline: scroll()`. The scroll container is `.landing-root` (the `<div>` with `overflow-y: auto` in `+layout.svelte`). The pipeline section uses a named `scroll-timeline` on its wrapper OR relies on `animation-range: entry/exit` percentages scoped to the section's viewport intersection. Each phase's content panel has `animation-range` set to its third of the scroll distance.

**Scroll container note:** `.landing-root` is the scrolling element (not `<html>`), because the landing layout sets `overflow-y: auto` on it. All `scroll()` references resolve to this element as the nearest scrolling ancestor.

**Fallback:** `@supports not (animation-timeline: scroll())` → remove sticky, show all 3 phases stacked vertically with `data-reveal` fade-up on each.

### Scroll-triggered reveals (all other sections)
Elements use `data-reveal` attribute with CSS `animation-timeline: view()` (existing system from `content-animations.css`). Stagger via `--i` custom property.

### Score bar animations (Section 3)
Bar widths animate from 0 to final width via `animation-timeline: view()`. Delta counters use `@property --num` animation.

### Logo strip (Section 4)
```css
.logo-strip {
  display: flex;
  gap: 40px;
  animation: scroll-logos 30s linear infinite;
}

@keyframes scroll-logos {
  to { transform: translateX(-50%); }
}
```
Logo strip is duplicated (2 copies side by side) so the loop appears seamless.

### Reduced motion
All animations respect `prefers-reduced-motion` via the global rule in `app.css` (duration → 0.01ms).

---

## Files to Create/Modify/Delete

### Create
- None new — all changes are to existing files

### Modify
- `src/routes/(landing)/+page.svelte` — complete rewrite with new sections
- `src/lib/components/landing/Footer.svelte` — trim to 2 columns + GitHub link in meta row
- `src/lib/components/landing/Navbar.svelte` — update anchor links to match new sections (`#pipeline`, `#example`, `#integrations`)
- `src/lib/styles/content-animations.css` — add pipeline scroll, logo strip, score bar keyframes
- `src/lib/content/types.ts` — remove `EndpointListSection`, `ArticleListSection`, `RoleListSection`, `ContactFormSection` interfaces and their `Section` union members
- `src/lib/components/landing/ContentPage.svelte` — remove imports and switch branches for the 4 deleted section types

### Delete (content pages)
- `src/lib/content/pages/pipeline.ts`
- `src/lib/content/pages/scoring.ts`
- `src/lib/content/pages/refinement.ts`
- `src/lib/content/pages/integrations.ts`
- `src/lib/content/pages/documentation.ts`
- `src/lib/content/pages/api-reference.ts`
- `src/lib/content/pages/mcp-server.ts`
- `src/lib/content/pages/about.ts`
- `src/lib/content/pages/blog.ts`
- `src/lib/content/pages/careers.ts`
- `src/lib/content/pages/contact.ts`

### Modify (page registry)
- `src/lib/content/pages.ts` — remove 11 deleted pages, keep 4 (privacy, terms, security, changelog)

### Delete (section components no longer needed)
- `src/lib/components/landing/sections/ArticleList.svelte`
- `src/lib/components/landing/sections/RoleList.svelte`
- `src/lib/components/landing/sections/ContactForm.svelte`
- `src/lib/components/landing/sections/EndpointList.svelte`

### Keep (still used by remaining 4 pages)
- `src/lib/components/landing/sections/HeroSection.svelte`
- `src/lib/components/landing/sections/ProseSection.svelte`
- `src/lib/components/landing/sections/StepFlow.svelte`
- `src/lib/components/landing/sections/CardGrid.svelte`
- `src/lib/components/landing/sections/CodeBlock.svelte`
- `src/lib/components/landing/sections/MetricBar.svelte`
- `src/lib/components/landing/sections/Timeline.svelte`
- `src/lib/components/landing/ContentPage.svelte`

### Delete (landing-only components replaced by inline sections)
- `src/lib/components/landing/FeatureCard.svelte`
- `src/lib/components/landing/TestimonialCard.svelte`
- `src/lib/components/landing/StepCard.svelte`

---

## Testing Criteria

1. `svelte-check` passes with 0 errors, 0 warnings
2. `vite build` succeeds (both normal and `GITHUB_PAGES=true`)
3. Root URL (`/`) shows the consolidated single-scroll landing page
4. All 6 sections render and scroll correctly
5. Scroll-pinned pipeline section transforms through 3 phases (Chrome/Edge/Safari 18+)
6. Fallback: pipeline shows stacked phases on unsupported browsers
7. Score bars animate on scroll
8. Logo strip scrolls infinitely without visual seam
9. 4 legal pages still accessible at `/privacy`, `/terms`, `/security`, `/changelog`
10. Removed slugs return 404
11. Footer links resolve correctly (anchors + legal pages)
12. Navbar anchor links scroll to correct sections
13. All text uses design system tokens (no hardcoded colors)
14. Responsive: mobile (< 640px), tablet (640-1024px), desktop (> 1024px)
15. `prefers-reduced-motion` disables all animations
