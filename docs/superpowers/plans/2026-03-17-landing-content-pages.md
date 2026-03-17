# Landing Content Pages Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 15 content pages served by a single dynamic template at `/landing/[slug]`, making every footer link functional.

**Architecture:** One dynamic SvelteKit route renders all pages from typed content objects. 11 section components handle different content shapes. CSS-native scroll-driven animations with progressive enhancement fallbacks.

**Tech Stack:** SvelteKit 2, Svelte 5 runes, TypeScript, CSS `animation-timeline: view()`, View Transitions API, `@starting-style`, container queries.

**Spec:** `docs/superpowers/specs/2026-03-17-landing-content-pages-design.md`

---

## Chunk 1: Foundation — Types, Route, Orchestrator, Motion CSS

### Task 1: Type System

**Files:**
- Create: `frontend/src/lib/content/types.ts`

- [ ] **Step 1: Create the content type definitions**

Create all 11 section interfaces, the `Section` discriminated union, and the `ContentPage` interface exactly as defined in the spec (lines 86-221). Export all types.

Key points:
- `CardGridSection.columns` is `2 | 3 | 5`
- `RoleListSection.type` is `'REMOTE' | 'HYBRID' | 'ON-SITE'` (union, not string)
- `EndpointListSection.method` includes `'SSE' | 'TOOL'` alongside HTTP methods
- `ContentPage` has `slug`, `title`, `description`, `sections: Section[]`

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/content/types.ts
git commit -m "feat(landing): add content page type system — 11 section types"
```

### Task 2: Dynamic Route Infrastructure

**Files:**
- Create: `frontend/src/routes/landing/[slug]/+page.ts`
- Create: `frontend/src/routes/landing/[slug]/+page.svelte`
- Create: `frontend/src/routes/landing/[slug]/+error.svelte`
- Create: `frontend/src/lib/content/pages.ts` (stub with 1 test page)

- [ ] **Step 1: Create pages.ts stub with one test page**

```typescript
// frontend/src/lib/content/pages.ts
import type { ContentPage } from './types';

const pipeline: ContentPage = {
  slug: 'pipeline',
  title: 'Three Phases. One Pipeline. Zero Guesswork.',
  description: 'Analyze, optimize, and score prompts through independent LLM subagents.',
  sections: [
    {
      type: 'hero',
      heading: 'THREE PHASES. ONE PIPELINE. ZERO GUESSWORK.',
      subheading: 'Each optimization runs through three independent LLM subagents — analyzer, optimizer, scorer — each with its own context window, rubric, and output contract.',
      cta: { label: 'Open the App', href: '/' },
    },
  ],
};

const allPages: Record<string, ContentPage> = { pipeline };

export function getPage(slug: string): ContentPage | undefined {
  return allPages[slug];
}

export function getAllSlugs(): string[] {
  return Object.keys(allPages);
}
```

- [ ] **Step 2: Create +page.ts loader**

```typescript
// frontend/src/routes/landing/[slug]/+page.ts
import { error } from '@sveltejs/kit';
import { getPage } from '$lib/content/pages';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ params }) => {
  const page = getPage(params.slug);
  if (!page) throw error(404, 'Page not found');
  return { page };
};
```

- [ ] **Step 3: Create +page.svelte (minimal — just renders title for now)**

```svelte
<script lang="ts">
  import Navbar from '$lib/components/landing/Navbar.svelte';
  import Footer from '$lib/components/landing/Footer.svelte';
  import type { ContentPage } from '$lib/content/types';

  let { data } = $props();
  const page: ContentPage = data.page;
</script>

<svelte:head>
  <title>{page.title} — Project Synthesis</title>
  <meta name="description" content={page.description} />
</svelte:head>

<Navbar />

<main id="main-content" class="content-page">
  <h1>{page.title}</h1>
  <p>Sections: {page.sections.length}</p>
</main>

<Footer />

<style>
  .content-page {
    max-width: 1120px;
    margin: 0 auto;
    padding: 52px 16px 40px;
  }
</style>
```

- [ ] **Step 4: Create +error.svelte (404 page)**

```svelte
<script lang="ts">
  import Navbar from '$lib/components/landing/Navbar.svelte';
  import Footer from '$lib/components/landing/Footer.svelte';
</script>

<Navbar />

<main id="main-content" class="error-page">
  <h1 class="error-page__heading">404 — PAGE NOT FOUND</h1>
  <p class="error-page__sub">This route doesn't exist in the pipeline.</p>
  <a href="/landing" class="error-page__cta">Back to Home</a>
</main>

<Footer />

<style>
  .error-page {
    max-width: 560px;
    margin: 0 auto;
    padding: 120px 16px 80px;
    text-align: center;
  }
  .error-page__heading {
    font-family: var(--font-display);
    font-size: 20px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    margin: 0 0 8px 0;
  }
  .error-page__sub {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0 0 16px 0;
  }
  .error-page__cta {
    display: inline-flex;
    align-items: center;
    height: 24px;
    padding: 0 12px;
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-sans);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-neon-cyan);
    border: 1px solid var(--color-neon-cyan);
    text-decoration: none;
    transition: all var(--duration-hover) var(--ease-spring);
  }
  .error-page__cta:hover {
    background: rgba(0, 229, 255, 0.08);
    transform: translateY(-1px);
  }
</style>
```

- [ ] **Step 5: Verify route works**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`
Expected: 0 errors, 0 warnings

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/landing/\[slug\]/ frontend/src/lib/content/
git commit -m "feat(landing): add dynamic route infrastructure — [slug] loader, 404 page"
```

### Task 3: Scroll Animation CSS

**Files:**
- Create: `frontend/src/lib/styles/content-animations.css`
- Modify: `frontend/src/routes/landing/+layout@.svelte` (import CSS + add view transitions)

- [ ] **Step 1: Create content-animations.css**

Contains all shared scroll-driven animation CSS from spec lines 259-357:
- `@supports (animation-timeline: view())` with `[data-reveal]` selector
- `reveal-up` keyframe
- Stagger via `--i` custom property
- `@property --num` counter animation
- Parallax keyframe
- `interpolate-size: allow-keywords` for `<details>` expand

Important: all animations are inside `@supports` — elements are visible by default without animation support.

**Note — dual animation system:** The existing landing page (`+page.svelte`) uses `data-animate` with IntersectionObserver for scroll reveals. Content pages use `data-reveal` with CSS `animation-timeline: view()`. Both coexist — `data-animate` stays on the landing root, `data-reveal` is used on all content pages. Do NOT migrate the landing root to `data-reveal` in this task.

- [ ] **Step 2: Update +layout@.svelte — import CSS + add view transitions**

Add to the `<script>` block:
```typescript
import '$lib/styles/content-animations.css';
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
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/styles/content-animations.css frontend/src/routes/landing/+layout@.svelte
git commit -m "feat(landing): add CSS scroll-driven animations + view transitions"
```

### Task 4: Section Components — Text Group (Hero, Prose, StepFlow)

**Files:**
- Create: `frontend/src/lib/components/landing/sections/HeroSection.svelte`
- Create: `frontend/src/lib/components/landing/sections/ProseSection.svelte`
- Create: `frontend/src/lib/components/landing/sections/StepFlow.svelte`

- [ ] **Step 1: Create HeroSection.svelte**

Props: `heading: string`, `subheading: string`, `cta?: { label: string; href: string }`.
- Heading: Syne uppercase, `letter-spacing: 0.1em`, `clamp(18px, 3vw, 28px)`. First line uses `text-gradient-forge`.
- Subheading: `text-secondary`, 12px.
- Optional CTA button (ghost style matching existing `btn-ghost` pattern).
- `view-transition-name: page-title` on heading for cross-page morph.
- `data-reveal` on the section wrapper for scroll animation.

- [ ] **Step 2: Create ProseSection.svelte**

Props: `blocks: Array<{ heading?: string; content: string }>`.
- Each block: optional bold heading in `text-primary`, content in `text-secondary`, 12px.
- Render `content` with `{@html}` (trusted content from data files).
- `data-reveal` on each block, staggered via `--i`.

- [ ] **Step 3: Create StepFlow.svelte**

Props: `steps: Array<{ title: string; description: string }>`.
- Reuse the existing StepCard visual pattern (number in Geist Mono circle, Syne title, connecting line).
- Render inline rather than delegating to StepCard component (avoid coupling to landing-page-specific component).
- `data-reveal` per step with stagger.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/landing/sections/
git commit -m "feat(landing): add HeroSection, ProseSection, StepFlow components"
```

### Task 5: Section Components — Data Group (CardGrid, CodeBlock, MetricBar)

**Files:**
- Create: `frontend/src/lib/components/landing/sections/CardGrid.svelte`
- Create: `frontend/src/lib/components/landing/sections/CodeBlock.svelte`
- Create: `frontend/src/lib/components/landing/sections/MetricBar.svelte`

- [ ] **Step 1: Create CardGrid.svelte**

Props: `columns: 2 | 3 | 5`, `cards: Array<{ icon?: string; color: string; title: string; description: string }>`.
- `container-type: inline-size` on grid wrapper.
- Grid columns via `grid-template-columns: repeat(N, 1fr)`.
- Container query: `@container (max-width: 640px)` → 1 col. `@container (max-width: 900px)` and columns=5 → 3 cols.
- Each card: reuse FeatureCard hover pattern (bg-card → bg-hover on hover, border-subtle → border-accent).
- Icon rendered via `{@html}`, colored via `style="color:{color}"`.
- `data-reveal` per card with `--i` stagger.

- [ ] **Step 2: Create CodeBlock.svelte**

Props: `language: string`, `code: string`, `filename?: string`.
- Container: `bg-input` background, `border-subtle` border, `font-mono`.
- Optional filename header bar above code (like mockup title bar pattern).
- Copy button in top-right corner: onclick copies `code` to clipboard, shows "Copied" flash (200ms).
- `white-space: pre-wrap` for code content.

- [ ] **Step 3: Create MetricBar.svelte**

Props: `dimensions: Array<{ name: string; value: number; color: string }>`, `label?: string`.
- Grid layout: `name (70px) | bar (1fr) | value (30px)`.
- Bar fill width = `value * 10%`, color from dimension data.
- Value in Geist Mono.
- Optional label above in `text-dim`.
- Bar fill animates width via `view()` timeline (in content-animations.css).

- [ ] **Step 4: Verify**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/landing/sections/
git commit -m "feat(landing): add CardGrid, CodeBlock, MetricBar components"
```

### Task 6: Section Components — Interactive Group (EndpointList, Timeline, ArticleList, RoleList, ContactForm)

**Files:**
- Create: `frontend/src/lib/components/landing/sections/EndpointList.svelte`
- Create: `frontend/src/lib/components/landing/sections/Timeline.svelte`
- Create: `frontend/src/lib/components/landing/sections/ArticleList.svelte`
- Create: `frontend/src/lib/components/landing/sections/RoleList.svelte`
- Create: `frontend/src/lib/components/landing/sections/ContactForm.svelte`

- [ ] **Step 1: Create EndpointList.svelte**

Props: `groups: Array<{ name: string; endpoints: Array<{ method, path, description, details? }> }>`.
- Group headings in Syne uppercase `text-[11px]`.
- Each endpoint: method badge (colored span: GET=`neon-blue`, POST=`neon-green`, PUT=`neon-orange`, DELETE=`neon-red`, PATCH=`neon-yellow`, SSE=`neon-cyan`, TOOL=`neon-cyan`) + monospace path + description.
- If `details` present, wrap in `<details>` with `interpolate-size: allow-keywords` for smooth expand per spec.
- `data-reveal` per group.

- [ ] **Step 2: Create Timeline.svelte**

Props: `versions: Array<{ version, date, categories: Array<{ label, color, items }> }>`.
- Vertical line on left (`border-left: 1px solid var(--color-border-subtle)`).
- Version badge: Geist Mono, `neon-cyan` border.
- Category labels (ADDED/CHANGED/FIXED/REMOVED): small uppercase text, transitions from `text-dim` to neon `color` via CSS `color` transition (NOT text-shadow).
- Items as bullet list in `text-secondary`.
- `data-reveal` per version entry.

- [ ] **Step 3: Create ArticleList.svelte**

Props: `articles: Array<{ title, excerpt, date, readTime, slug? }>`.
- Card per article: title (`text-primary`, weight 600), excerpt (`text-secondary`), date + readTime in Geist Mono (`text-dim`).
- "Coming soon" badge in top-right of each card (Geist Mono, `text-dim`, `border-subtle`).
- Hover: `translateY(-1px)` + `border-accent`.
- `data-reveal` per card with stagger.

- [ ] **Step 4: Create RoleList.svelte**

Props: `roles: Array<{ title, description, type, department }>`.
- Card per role: title (`text-primary`, weight 600), description (`text-secondary`).
- Type badge (REMOTE in `neon-green` border, small monospace).
- Department in Geist Mono `text-dim`.
- `data-reveal` per card.

- [ ] **Step 5: Create ContactForm.svelte**

Props: `categories: string[]`, `successMessage: string`.
- State: `submitted = $state(false)`.
- Fields: name input, email input, category `<select>`, message `<textarea>`.
- All use existing input styles from `app.css` (bg-input, border-subtle, 11px).
- Submit button: solid cyan fill (matching `btn-primary` pattern).
- On submit: `event.preventDefault()`, set `submitted = true`.
- Success state: message in `text-secondary` with spring fade-in via `@starting-style`.
- Form never sends data (frontend-only demo).

- [ ] **Step 6: Verify**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/components/landing/sections/
git commit -m "feat(landing): add EndpointList, Timeline, ArticleList, RoleList, ContactForm"
```

### Task 7: ContentPage Orchestrator

**Files:**
- Create: `frontend/src/lib/components/landing/ContentPage.svelte`
- Modify: `frontend/src/routes/landing/[slug]/+page.svelte` (use ContentPage)

- [ ] **Step 1: Create ContentPage.svelte**

The orchestrator component. Receives `sections: Section[]` and renders the correct component for each section type using Svelte's `{#if}` / `{:else if}` chain on `section.type`.

```svelte
<script lang="ts">
  import type { Section } from '$lib/content/types';
  import HeroSection from './sections/HeroSection.svelte';
  import ProseSection from './sections/ProseSection.svelte';
  import CardGrid from './sections/CardGrid.svelte';
  import EndpointList from './sections/EndpointList.svelte';
  import Timeline from './sections/Timeline.svelte';
  import ArticleList from './sections/ArticleList.svelte';
  import RoleList from './sections/RoleList.svelte';
  import ContactForm from './sections/ContactForm.svelte';
  import StepFlow from './sections/StepFlow.svelte';
  import CodeBlock from './sections/CodeBlock.svelte';
  import MetricBar from './sections/MetricBar.svelte';

  interface Props { sections: Section[]; }
  let { sections }: Props = $props();
</script>

{#each sections as section}
  <section class="content-section">
    {#if section.type === 'hero'}
      <HeroSection {...section} />
    {:else if section.type === 'prose'}
      <ProseSection {...section} />
    <!-- ... all 11 types ... -->
    {/if}
  </section>
{/each}
```

- [ ] **Step 2: Update +page.svelte to use ContentPage**

Replace the placeholder `<h1>` with `<ContentPage sections={page.sections} />`. Keep Navbar, Footer, `<svelte:head>`.

- [ ] **Step 3: Verify pipeline page renders**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`
Then: `npx vite build 2>&1 | tail -3` — should succeed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/landing/ContentPage.svelte frontend/src/routes/landing/\[slug\]/+page.svelte
git commit -m "feat(landing): add ContentPage orchestrator — switches on section type"
```

---

## Chunk 2: Content Data — All 15 Pages

### Task 8: Product Pages Content (4 pages)

**Files:**
- Create: `frontend/src/lib/content/pages/pipeline.ts`
- Create: `frontend/src/lib/content/pages/scoring.ts`
- Create: `frontend/src/lib/content/pages/refinement.ts`
- Create: `frontend/src/lib/content/pages/integrations.ts`
- Modify: `frontend/src/lib/content/pages.ts` (import and register all 4)

- [ ] **Step 1: Create pipeline.ts**

Content per spec lines 366-374. Sections: hero, step-flow (3 phases), card-grid (3 col: Isolated Context, Bias Mitigation, Strategy Adaptive), code-block (synthesis_optimize example).

- [ ] **Step 2: Create scoring.ts**

Content per spec lines 376-385. Sections: hero, card-grid (5 col: 5 dimension cards with chromatic colors), metric-bar (before — low scores), metric-bar (after — high scores), prose (hybrid methodology).

- [ ] **Step 3: Create refinement.ts**

Content per spec lines 387-394. Sections: hero, step-flow (4 steps), card-grid (2 col: Version History, Smart Suggestions).

- [ ] **Step 4: Create integrations.ts**

Content per spec lines 396-405. Sections: hero, card-grid (3 col: MCP/GitHub/Docker), code-block (.mcp.json), code-block (.env), code-block (docker compose).

- [ ] **Step 5: Register in pages.ts**

Import all 4 from `./pages/`, add to `allPages` record. **Remove the inline `pipeline` stub** from Task 2 — replace it with the import from `./pages/pipeline`.

- [ ] **Step 6: Verify all 4 slugs resolve**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/content/pages/
git commit -m "feat(landing): add Product content pages — pipeline, scoring, refinement, integrations"
```

### Task 9: Resources Pages Content (4 pages)

**Files:**
- Create: `frontend/src/lib/content/pages/documentation.ts`
- Create: `frontend/src/lib/content/pages/api-reference.ts`
- Create: `frontend/src/lib/content/pages/mcp-server.ts`
- Create: `frontend/src/lib/content/pages/changelog.ts`
- Modify: `frontend/src/lib/content/pages.ts` (register all 4)

- [ ] **Step 1: Create documentation.ts**

Content per spec lines 409-416. Sections: hero, card-grid (3 col, 6 cards: Quickstart, Architecture, Configuration, Contributing, Prompt Templates, Deployment), code-block (git clone + init.sh).

- [ ] **Step 2: Create api-reference.ts**

Content per spec lines 418-424. Sections: hero, endpoint-list (all endpoints grouped by router from the spec — Optimize, Refinement, History, Feedback, Providers, Preferences, Strategies, Settings, GitHub Auth, GitHub Repos, Health, Events). Reference `CLAUDE.md` for exact endpoint paths and methods.

- [ ] **Step 3: Create mcp-server.ts**

Content per spec lines 426-434. Sections: hero, endpoint-list (4 TOOL entries), code-block (.mcp.json), prose (passthrough workflow).

- [ ] **Step 4: Create changelog.ts**

Content per spec lines 436-442. Sections: hero, timeline (v2.0.0 unreleased + v0.7.0). Pull categories from existing `CHANGELOG.md`.

- [ ] **Step 5: Register in pages.ts and verify**

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/content/pages/
git commit -m "feat(landing): add Resources content pages — docs, api-reference, mcp-server, changelog"
```

### Task 10: Company Pages Content (4 pages)

**Files:**
- Create: `frontend/src/lib/content/pages/about.ts`
- Create: `frontend/src/lib/content/pages/blog.ts`
- Create: `frontend/src/lib/content/pages/careers.ts`
- Create: `frontend/src/lib/content/pages/contact.ts`
- Modify: `frontend/src/lib/content/pages.ts` (register all 4)

- [ ] **Step 1: Create about.ts**

Content per spec lines 446-453. Sections: hero, prose (origin story — 3 blocks: the problem, the approach, open source), card-grid (3 col values: Measure Everything, Zero Trust in Self-Rating, Your Infrastructure).

- [ ] **Step 2: Create blog.ts**

Content per spec lines 455-461. Sections: hero, article-list (3 seed articles with excerpts, dates, read times — "Coming soon" handled by component).

- [ ] **Step 3: Create careers.ts**

Content per spec lines 463-470. Sections: hero, prose (culture — 2 blocks), role-list (3 REMOTE roles: Senior Backend, Frontend, ML/Evaluation).

- [ ] **Step 4: Create contact.ts**

Content per spec lines 472-479. Sections: hero, card-grid (2 col, 4 channel cards), contact-form (categories: Bug Report, Feature Request, Security, General; success message about demo form).

- [ ] **Step 5: Register in pages.ts and verify**

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/content/pages/
git commit -m "feat(landing): add Company content pages — about, blog, careers, contact"
```

### Task 11: Legal Pages Content (3 pages)

**Files:**
- Create: `frontend/src/lib/content/pages/privacy.ts`
- Create: `frontend/src/lib/content/pages/terms.ts`
- Create: `frontend/src/lib/content/pages/security.ts`
- Modify: `frontend/src/lib/content/pages.ts` (register all 3)

- [ ] **Step 1: Create privacy.ts**

Content per spec lines 483-489. Sections: hero, prose (6 blocks: Data Processing, LLM Provider Communication, GitHub Integration, Secrets Management, No Cloud Services, Data Retention).

- [ ] **Step 2: Create terms.ts**

Content per spec lines 491-497. Sections: hero, prose (5 blocks sourced from existing `docs/TERMS.md`: License, Free for Everyone, No SLA, Sustainability, Contributions).

- [ ] **Step 3: Create security.ts**

Content per spec lines 499-506. Sections: hero, step-flow (4 reporting steps), card-grid (2 col, 4 cards: Encryption at Rest, Input Validation, Workspace Isolation, No External Dependencies). Source from existing `SECURITY.md`.

- [ ] **Step 4: Register in pages.ts and verify**

All 15 pages should now be registered. Verify: `getAllSlugs().length === 15`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/content/pages/
git commit -m "feat(landing): add Legal content pages — privacy, terms, security"
```

---

## Chunk 3: Wiring, Polish, Verification

### Task 12: Footer + Navbar Updates

**Files:**
- Modify: `frontend/src/lib/components/landing/Footer.svelte`
- Modify: `frontend/src/lib/components/landing/Navbar.svelte`

- [ ] **Step 1: Update Footer.svelte links**

Replace all `href: '#'` and `href: '#features'` with actual `/landing/[slug]` paths per spec lines 514-551.

- [ ] **Step 2: Update Navbar.svelte for content page detection**

Add `page` store import. Derive `isContentPage` from pathname. When on a content page, anchor links become absolute (`/landing#features`). Add a "Home" text link before the anchor links when on content pages.

```typescript
import { page } from '$app/stores';
const isContentPage = $derived($page.url.pathname !== '/landing');
```

Update each `navLinks` href: `isContentPage ? '/landing#features' : '#features'`.

- [ ] **Step 3: Verify navigation**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -3`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/landing/Footer.svelte frontend/src/lib/components/landing/Navbar.svelte
git commit -m "feat(landing): wire footer links to content pages, fix navbar for subpages"
```

### Task 13: Build Verification

- [ ] **Step 1: Type check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json 2>&1 | tail -5`
Expected: 0 errors, 0 warnings

- [ ] **Step 2: Production build**

Run: `cd frontend && npx vite build 2>&1 | tail -5`
Expected: Build succeeds, "Wrote site to build"

- [ ] **Step 3: Verify all 15 slugs are in the build**

Run: `ls frontend/build/landing/ | sort`
Expected: 15 directories matching the slug names

- [ ] **Step 4: Verify unknown slug returns 404**

Check that `frontend/src/routes/landing/[slug]/+error.svelte` exists and the loader throws `error(404)` for unknown slugs.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(landing): complete 15-page content system — all footer links functional"
```
