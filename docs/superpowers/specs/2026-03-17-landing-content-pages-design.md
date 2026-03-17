# Landing Page Content System — Design Spec

**Date:** 2026-03-17
**Status:** Approved
**Scope:** 15 content pages served by a single dynamic template at `/landing/[slug]`

---

## Overview

The landing page footer has 15 links pointing to `#` placeholders. This spec designs a content system that makes every link functional with production-quality pages — some sourced from existing codebase documentation, others created as realistic business content.

**Architecture:** Single dynamic route (`/landing/[slug]/+page.svelte`) renders all 16 pages from typed content objects. A shared template switches on 11 section types, each backed by a small Svelte component.

---

## Route Structure

```
frontend/src/
  routes/landing/
    [slug]/
      +page.svelte          # Dynamic template — renders any page from content data
      +page.ts              # load() resolves slug → ContentPage object, error(404) on unknown
      +error.svelte         # 404 page — "Page not found" in design system style
  lib/
    content/
      types.ts              # ContentPage, Section discriminated union, per-section types
      pages.ts              # All 15 page definitions as typed objects
      pages/                # One file per page when content exceeds 80 lines
        pipeline.ts
        scoring.ts
        api-reference.ts
        ...
    components/landing/
      sections/
        HeroSection.svelte
        ProseSection.svelte
        CardGrid.svelte
        EndpointList.svelte
        Timeline.svelte
        ArticleList.svelte
        RoleList.svelte
        ContactForm.svelte
        StepFlow.svelte
        CodeBlock.svelte
        MetricBar.svelte
      ContentPage.svelte    # The orchestrator — iterates sections, renders the right component
```

### 404 Error Page

`[slug]/+error.svelte` renders a design-system-compliant 404 with Navbar + Footer:

- Heading: "404 — PAGE NOT FOUND" (Syne uppercase)
- Subtext: "This route doesn't exist in the pipeline." (`text-secondary`)
- CTA: "Back to Home" button → `/landing`
- Uses the landing `+layout@.svelte` (scrollable, no workbench)

### Navbar on Content Pages

`Navbar.svelte` detects whether it's on the landing root (`/landing`) or a content subpage (`/landing/[slug]`). On content pages, anchor links (`#features`, etc.) become absolute links (`/landing#features`) so they navigate back to the landing page sections. The "Get Started" CTA remains unchanged.

```typescript
// Navbar.svelte — detect context
import { page } from '$app/stores';
const isContentPage = $derived($page.url.pathname !== '/landing');
// Links: isContentPage ? '/landing#features' : '#features'
```

### Per-Page `<svelte:head>`

`ContentPage.svelte` (or `[slug]/+page.svelte`) sets per-page title and description from the `ContentPage` data object, overriding the static title in `+layout@.svelte`:

```svelte
<svelte:head>
  <title>{page.title} — Project Synthesis</title>
  <meta name="description" content={page.description} />
</svelte:head>
```

---

## Type System

```typescript
// types.ts

type SectionType =
  | 'hero'
  | 'prose'
  | 'card-grid'
  | 'endpoint-list'
  | 'timeline'
  | 'article-list'
  | 'role-list'
  | 'contact-form'
  | 'step-flow'
  | 'code-block'
  | 'metric-bar';

interface HeroSection {
  type: 'hero';
  heading: string;
  subheading: string;
  cta?: { label: string; href: string };
}

interface ProseSection {
  type: 'prose';
  blocks: Array<{ heading?: string; content: string }>;
}

interface CardGridSection {
  type: 'card-grid';
  columns: 2 | 3 | 5;       // Responsive reflow: 5→3+2 at <1024px, all→1 at <640px
  cards: Array<{
    icon?: string;           // Inline SVG string (same format as FeatureCard)
    color: string;           // CSS var reference, e.g., 'var(--color-neon-cyan)'
    title: string;
    description: string;
  }>;
}

interface EndpointListSection {
  type: 'endpoint-list';
  groups: Array<{
    name: string;
    endpoints: Array<{
      method: 'GET' | 'POST' | 'PATCH' | 'DELETE' | 'PUT' | 'SSE' | 'TOOL';
      path: string;
      description: string;
      details?: string;    // Expandable content (params, response shape, curl)
    }>;
  }>;
}

interface TimelineSection {
  type: 'timeline';
  versions: Array<{
    version: string;
    date: string;
    categories: Array<{
      label: 'ADDED' | 'CHANGED' | 'FIXED' | 'REMOVED';
      color: string;
      items: string[];
    }>;
  }>;
}

interface ArticleListSection {
  type: 'article-list';
  articles: Array<{
    title: string;
    excerpt: string;
    date: string;
    readTime: string;
    slug?: string;         // Future: link to full article route
  }>;
}

interface RoleListSection {
  type: 'role-list';
  roles: Array<{
    title: string;
    description: string;
    type: 'REMOTE' | 'HYBRID' | 'ON-SITE';
    department: string;
  }>;
}

interface ContactFormSection {
  type: 'contact-form';
  categories: string[];    // Dropdown options
  successMessage: string;
}

interface StepFlowSection {
  type: 'step-flow';
  steps: Array<{
    title: string;
    description: string;
  }>;
}

interface CodeBlockSection {
  type: 'code-block';
  language: string;
  code: string;
  filename?: string;
}

interface MetricBarSection {
  type: 'metric-bar';
  dimensions: Array<{
    name: string;
    value: number;         // 0-10
    color: string;
  }>;
  label?: string;          // e.g., "Original prompt" or "After optimization"
}

type Section =
  | HeroSection
  | ProseSection
  | CardGridSection
  | EndpointListSection
  | TimelineSection
  | ArticleListSection
  | RoleListSection
  | ContactFormSection
  | StepFlowSection
  | CodeBlockSection
  | MetricBarSection;

interface ContentPage {
  slug: string;
  title: string;            // <title> tag
  description: string;      // <meta description>
  sections: Section[];
}
```

---

## Section Components

Each section component receives its typed props and renders with design-system compliance. All components use CSS custom properties from `app.css`. No Tailwind utility classes for layout — scoped `<style>` blocks only, matching existing component patterns.

### Shared behavior

- **Scroll animation:** All sections use CSS `animation-timeline: view()` for entrance. Fallback: elements visible by default (animation is progressive enhancement via `@supports`).
- **Stagger:** Cards and list items stagger via `--i` custom property: `animation-delay: calc(var(--i) * 80ms)`.
- **Reduced motion:** Global `prefers-reduced-motion` rule in `app.css` already handles this.
- **Container queries:** CardGrid and EndpointList use `container-type: inline-size` so cards adapt to container width, not viewport.

### Component-specific notes

| Component | Key behavior |
|-----------|-------------|
| `HeroSection` | Syne uppercase heading, gradient text on first page word, optional CTA button. Parallax at 0.3x via `scroll()` timeline. |
| `ProseSection` | Structured blocks with optional bold headings. Used for legal, about, culture content. Minimal animation — fade-up only. |
| `CardGrid` | Responsive columns via container query. Reuses FeatureCard hover pattern (border accent + bg shift). |
| `EndpointList` | `<details>` elements with `interpolate-size: allow-keywords` for smooth expand. Method badges color-coded (GET=blue, POST=green, PUT=orange, DELETE=red, PATCH=yellow, SSE=cyan, TOOL=cyan). |
| `Timeline` | Vertical line with version badges. Category labels (ADDED/CHANGED/FIXED) transition from `text-dim` to their neon `color` on entry (CSS `color` transition, not text-shadow/glow). |
| `ArticleList` | Cards with date + excerpt + read time. Date in Geist Mono. Hover lifts `translateY(-1px)`. |
| `RoleList` | Job cards with type badge (REMOTE in green). Department in Geist Mono dim. |
| `ContactForm` | Frontend-only. Name, email, category select, message textarea. Submit shows success state with spring animation. No backend handler. |
| `StepFlow` | Reuses StepCard pattern from landing page. Numbers in Geist Mono, titles in Syne uppercase. Connecting lines stretch via `flex: 1`. |
| `CodeBlock` | Monospace on `bg-input` background. Copy button in top-right. Optional filename header. CSS `steps()` typewriter effect on scroll for hero code blocks. |
| `MetricBar` | Horizontal bars with dimension name + score value in Geist Mono. Bar fills animate width on scroll via `view()` timeline. |

---

## Modern Motion System

All animations use CSS-native features. No JavaScript animation libraries.

### Scroll-driven animations (CSS `animation-timeline: view()`)

```css
@supports (animation-timeline: view()) {
  [data-reveal] {
    animation: reveal-up 1s var(--ease-spring) both;
    animation-timeline: view();
    animation-range: entry 0% entry 100%;
  }
}

@keyframes reveal-up {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
}
```

Fallback for unsupported browsers: elements render immediately (no `opacity: 0` default). Animation is progressive enhancement.

### View Transitions (page-to-page)

```svelte
<!-- Added to existing +layout@.svelte (append to <script>, preserve existing content) -->
<script lang="ts">
  import { onNavigate } from '$app/navigation';

  onNavigate((navigation) => {
    if (!document.startViewTransition) return;
    return new Promise((resolve) => {
      document.startViewTransition(async () => {
        resolve();
        await navigation.complete;
      });
    });
  });
</script>
```

Hero headings get `view-transition-name: page-title` so they morph between pages instead of hard-cutting.

### CSS `@starting-style` (entry animations)

Used for `<details>` expand and contact form success state:

```css
details[open] > .details-content {
  opacity: 1;
  height: auto;

  @starting-style {
    opacity: 0;
    height: 0;
  }

  transition: opacity 300ms var(--ease-spring), height 300ms var(--ease-spring);
  interpolate-size: allow-keywords;
}
```

### Scroll-linked parallax

Hero sections only. Background elements translate at 0.3x scroll rate:

```css
@supports (animation-timeline: scroll()) {
  .hero__parallax-layer {
    animation: parallax linear both;
    animation-timeline: scroll();
  }
}

@keyframes parallax {
  to { transform: translateY(calc(var(--parallax-distance, 40px))); }
}
```

### Counter animation (metric numbers)

Social proof bar and score values count up from 0:

```css
@property --num {
  syntax: '<integer>';
  initial-value: 0;
  inherits: false;
}

.counter {
  animation: count-up 1.5s var(--ease-spring) both;
  animation-timeline: view();
  counter-reset: num var(--num);
}

@keyframes count-up {
  from { --num: 0; }
  to { --num: var(--target); }
}
```

---

## Page Content Specs

### PRODUCT

#### `/landing/pipeline`
**Title:** Three Phases. One Pipeline. Zero Guesswork.

| Section | Content |
|---------|---------|
| hero | Heading: "THREE PHASES. ONE PIPELINE. ZERO GUESSWORK." Sub: "Each optimization runs through three independent LLM subagents..." CTA: "Open the App" → `/` |
| step-flow | 3 steps: ANALYZE (classify, detect weaknesses, select strategy), OPTIMIZE (rewrite with strategy + context injection), SCORE (blind A/B eval, hybrid blending, drift detection) |
| card-grid (3 col) | Isolated Context, Bias Mitigation, Strategy Adaptive |
| code-block | `synthesis_optimize(prompt="...", strategy="chain-of-thought")` |

#### `/landing/scoring`
**Title:** Five Dimensions. Hybrid Engine. No Self-Rating Bias.

| Section | Content |
|---------|---------|
| hero | Heading: "FIVE DIMENSIONS. HYBRID ENGINE. NO SELF-RATING BIAS." |
| card-grid (5 col) | Clarity (cyan), Specificity (purple), Structure (green), Faithfulness (yellow), Conciseness (pink) — each with one-line definition. Responsive: 5→3+2 at tablet, 1-col at mobile. |
| metric-bar | "Before" — original prompt scores (low). Label: "Original: write some code to handle user data" |
| metric-bar | "After" — optimized prompt scores (high). Label: "Optimized: Write a Python function validate_user(data: dict)..." Shows score lift per dimension. |
| prose | Hybrid methodology: dimension weights, z-score normalization threshold (≥10 samples), divergence flagging (>2.5pt delta), passthrough bias correction (0.85). |

#### `/landing/refinement`
**Title:** Branch. Refine. Converge.

| Section | Content |
|---------|---------|
| hero | Heading: "BRANCH. REFINE. CONVERGE." Sub: "Each turn is a fresh pipeline invocation — not accumulated context..." |
| step-flow | 4 steps: Initial Turn, Refine, Branch/Rollback, Converge |
| card-grid (2 col) | Version History, Smart Suggestions |

#### `/landing/integrations`
**Title:** Plug In Anywhere.

| Section | Content |
|---------|---------|
| hero | Heading: "PLUG IN ANYWHERE." Sub: "MCP server for Claude Code. GitHub OAuth for codebase-aware optimization. Docker for one-command deployment." |
| card-grid (3 col) | MCP Server (cyan), GitHub OAuth (purple), Docker (green) |
| code-block | `.mcp.json` config snippet (filename: ".mcp.json") |
| code-block | GitHub OAuth env vars (filename: ".env") |
| code-block | `docker compose up --build -d` (filename: "terminal") |

### RESOURCES

#### `/landing/documentation`
**Title:** Everything You Need to Ship.

| Section | Content |
|---------|---------|
| hero | Heading: "EVERYTHING YOU NEED TO SHIP." |
| card-grid (3×2) | Quickstart, Architecture, Configuration, Contributing, Prompt Templates, Deployment |
| code-block | `git clone && cd PromptForge_v2 && ./init.sh` |

#### `/landing/api-reference`
**Title:** Every Endpoint. Every Parameter.

| Section | Content |
|---------|---------|
| hero | Heading: "EVERY ENDPOINT. EVERY PARAMETER." Sub: "REST API on port 8000..." |
| endpoint-list | 24+ endpoints grouped by router: Optimize (POST /api/optimize, GET /api/optimize/{trace_id}, POST /api/optimize/passthrough, POST /api/optimize/passthrough/save), Refinement (POST /api/refine, GET /api/refine/{id}/versions, POST /api/refine/{id}/rollback), History (GET /api/history), Feedback (POST /api/feedback, GET /api/feedback), Providers (GET /api/providers, GET/PATCH/DELETE /api/provider/api-key), Preferences (GET/PATCH /api/preferences), Strategies (GET /api/strategies, GET/PUT /api/strategies/{name}), Settings (GET /api/settings), GitHub Auth (GET login, GET callback, GET me, POST logout), GitHub Repos (GET list, POST link, GET linked, DELETE unlink), Health (GET /api/health), Events (GET /api/events SSE, POST /api/events/_publish) |

#### `/landing/mcp-server`
**Title:** Optimize Without Leaving Your Editor.

| Section | Content |
|---------|---------|
| hero | Heading: "OPTIMIZE WITHOUT LEAVING YOUR EDITOR." |
| endpoint-list | 4 TOOL entries: synthesis_optimize, synthesis_analyze, synthesis_prepare_optimization, synthesis_save_result |
| code-block | `.mcp.json` one-liner |
| prose | Passthrough workflow: prepare → external LLM → save |

#### `/landing/changelog`
**Title:** What Changed and When.

| Section | Content |
|---------|---------|
| hero | Heading: "WHAT CHANGED AND WHEN." |
| timeline | v2.0.0 (Unreleased): hybrid scoring, MCP server, refinement branching, event bus, provider error hierarchy, XML scorer delimiters. v0.7.0 (2026-02-15): initial release. |

### COMPANY

#### `/landing/about`
**Title:** Built by Engineers Who Got Tired of Vague Prompts.

| Section | Content |
|---------|---------|
| hero | Heading: "BUILT BY ENGINEERS WHO GOT TIRED OF VAGUE PROMPTS." |
| prose | Origin story: the problem (prompts never measured), the approach (optimization as compilation), open source (Apache 2.0, self-hosted, no data leaves your infra) |
| card-grid (3 col) | Values: Measure Everything, Zero Trust in Self-Rating, Your Infrastructure |

#### `/landing/blog`
**Title:** From the Pipeline.

| Section | Content |
|---------|---------|
| hero | Heading: "FROM THE PIPELINE." Sub: "Technical deep-dives, methodology breakdowns, and integration guides." |
| article-list | 3 seed articles (excerpt-only, no full article routes — cards show "Coming soon" indicator): "Introducing Project Synthesis v2" (2026-03-15, 8min), "The Scoring Problem: Why LLMs Can't Grade Their Own Work" (2026-03-08, 12min), "MCP in Practice: Optimizing Prompts Without Leaving Claude Code" (2026-02-28, 6min) |

#### `/landing/careers`
**Title:** Build the Tools That Build Better Prompts.

| Section | Content |
|---------|---------|
| hero | Heading: "BUILD THE TOOLS THAT BUILD BETTER PROMPTS." |
| prose | Culture: fully remote, async-first, documentation-heavy. Specs before code. Trace logs instead of standups. |
| role-list | 3 roles: Senior Backend Engineer (Python, FastAPI), Frontend Engineer (SvelteKit, Tailwind), ML/Evaluation Engineer (scoring calibration, embeddings) — all REMOTE |

#### `/landing/contact`
**Title:** Signal, Not Noise.

| Section | Content |
|---------|---------|
| hero | Heading: "SIGNAL, NOT NOISE." |
| card-grid (2×2) | Bug Reports (GitHub Issues, red), Feature Requests (GitHub Discussions, purple), Security (private reporting, orange), General (email, cyan) |
| contact-form | Fields: name, email, category (Bug Report/Feature Request/Security/General), message. Frontend-only demo form. Success: "Demo form — for real inquiries, contact support@zenresources.net" |

### LEGAL

#### `/landing/privacy`
**Title:** Your Prompts. Your Infrastructure. Your Data.

| Section | Content |
|---------|---------|
| hero | Heading: "YOUR PROMPTS. YOUR INFRASTRUCTURE. YOUR DATA." |
| prose | 6 sections: Data Processing (local SQLite), LLM Provider Communication (no proxy/intercept), GitHub Integration (Fernet-encrypted tokens), Secrets Management (auto-generated keys), No Cloud Services (no SaaS dependency), Data Retention (configurable trace rotation) |

#### `/landing/terms`
**Title:** Open Source. Open Terms.

| Section | Content |
|---------|---------|
| hero | Heading: "OPEN SOURCE. OPEN TERMS." |
| prose | Content from existing `docs/TERMS.md`: Apache 2.0 license, free for everyone, no SLA, sustainability model, contribution governance |

#### `/landing/security`
**Title:** Defense in Depth.

| Section | Content |
|---------|---------|
| hero | Heading: "DEFENSE IN DEPTH." |
| step-flow | 4 steps: Report privately, Team acknowledges (72h), Fix in private branch, Advisory published |
| card-grid (2×2) | Encryption at Rest, Input Validation, Workspace Isolation, No External Dependencies |

---

## Footer Link Updates

Update `Footer.svelte` to point all links to `/landing/[slug]`:

```typescript
const columns = [
  {
    title: 'Product',
    links: [
      { label: 'Pipeline', href: '/landing/pipeline' },
      { label: 'Scoring', href: '/landing/scoring' },
      { label: 'Refinement', href: '/landing/refinement' },
      { label: 'Integrations', href: '/landing/integrations' },
    ],
  },
  {
    title: 'Resources',
    links: [
      { label: 'Documentation', href: '/landing/documentation' },
      { label: 'API Reference', href: '/landing/api-reference' },
      { label: 'MCP Server', href: '/landing/mcp-server' },
      { label: 'Changelog', href: '/landing/changelog' },
    ],
  },
  {
    title: 'Company',
    links: [
      { label: 'About', href: '/landing/about' },
      { label: 'Blog', href: '/landing/blog' },
      { label: 'Careers', href: '/landing/careers' },
      { label: 'Contact', href: '/landing/contact' },
    ],
  },
  {
    title: 'Legal',
    links: [
      { label: 'Privacy', href: '/landing/privacy' },
      { label: 'Terms', href: '/landing/terms' },
      { label: 'Security', href: '/landing/security' },
    ],
  },
];
```

---

## Testing Criteria

1. All 16 slugs resolve to a rendered page (no 404s)
2. Unknown slugs return 404
3. Every page has a valid `<title>` and `<meta description>`
4. `svelte-check` passes with 0 errors, 0 warnings
5. `vite build` succeeds
6. View transitions work between content pages
7. Scroll animations degrade gracefully (elements visible without JS/CSS animation support)
8. Contact form shows success state on submit
9. Endpoint details expand/collapse smoothly
10. All text uses design system fonts and colors (no hardcoded hex outside tokens)
11. Responsive: mobile (< 640px), tablet (640–1024px), desktop (> 1024px)
12. Keyboard navigation works: all interactive elements focusable, skip-link present, focus ring visible
