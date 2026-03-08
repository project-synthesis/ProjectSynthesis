# Project Synthesis — UI/UX Design Architecture

> **Purpose:** This document is the authoritative reference for all UI/UX, layout, and interaction design decisions in Project Synthesis. It replaces the "traditional web app" and "OS window manager" approaches with a developer-workbench model purpose-built for prompt engineering, chaining, IDE integration, and organization.
>
> Every front-end decision — component structure, layout, interaction, state, motion — should trace back to a principle or pattern defined here.

---

## 1. The Design Thesis

### What We Are Not Building

**Not a traditional web app.** No top navigation bar with links. No hero section with a tagline and CTA button. No sidebar accordion menu. No card grid landing page. These patterns optimize for marketing and discoverability by casual users — the wrong audience.

**Not a full OS.** No floating windows, no window manager, no drag-to-arrange desktop, no minimize/maximize buttons. The initial version was ambitious and educational; v2 is focused. We do not need a taskbar.

### What We Are Building

A **developer workbench** — a persistent, data-rich engineering environment where prompt documents, pipeline execution, optimization history, and codebase context all live in a unified spatial layout. The mental model is closer to VS Code + LangSmith than to a SaaS app dashboard.

The workbench has one job: help engineers build, run, evaluate, and evolve prompts with the same rigor they apply to code.

### The Five Core Principles

1. **The prompt is the document.** Everything — pipeline runs, scores, version history — attaches to a prompt file as metadata. You don't "navigate to history"; history lives inside the prompt.

2. **Pipeline is a view, not a destination.** The Analyze→Strategy→Optimize→Validate pipeline is not a separate screen. It is the "Pipeline" sub-tab of the current prompt document, shown live during execution.

3. **Speed is a feature.** Every UI action must respond in under 100ms (optimistic update or skeleton state). Zero spinners for navigation. Streaming output per stage, not "wait for completion."

4. **Keyboard-first, mouse-possible.** The primary workflow loop — compose, forge, review, copy — must be completable without touching the mouse. The UI educates keyboard shortcuts through natural use (show them in tooltips and the command palette).

5. **Color is data, not decoration.** Every neon color has a fixed semantic role (see brand-guidelines). The UI does not use color to "make things pop." It uses color to encode information.

---

## 2. The 5-Zone Workbench Layout

```
┌──────┬────────────────┬──────────────────────────────────────────┬─────────────────┐
│      │                │                                          │                 │
│  A   │   NAVIGATOR    │           EDITOR  GROUPS                 │   INSPECTOR     │
│  C   │   (240px)      │           (fills)                        │   (280px)       │
│  T   │                │                                          │                 │
│  I   │  [Project]     │  [Tab: README.md] [Tab: prompt.md ●]    │  context-aware  │
│  V   │  [History]     │                                          │  metadata,      │
│  I   │  [Templates]   │  ┌──────────────────────────────────┐   │  scores,        │
│  T   │  [Chains]      │  │  DOCUMENT EDITOR / STAGE TRACK   │   │  strategy,      │
│  Y   │                │  │                                  │   │  actions        │
│      │                │  │  [Edit] [Pipeline] [History]     │   │                 │
│  B   │                │  │                                  │   │                 │
│  A   │                │  │  < prompt text or live pipeline >│   │                 │
│  R   │                │  │                                  │   │                 │
│      │                │  └──────────────────────────────────┘   │                 │
├──────┴────────────────┴──────────────────────────────────────────┴─────────────────┤
│ STATUS BAR:  ⬡ octocat/repo@main  │  chain-of-thought  │  8.2/10  │  CLI ●       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Zone Breakdown

| Zone | Width | Role | Collapses? |
|------|-------|------|------------|
| Activity Bar | 40px fixed | Icon-only panel switcher | No (always visible) |
| Navigator | 240px default, resizable | Tree/list for current activity | Yes (keyboard: `Ctrl+B`) |
| Editor Groups | fills remaining | Tabbed document work surface | No |
| Inspector | 280px default, resizable | Context-sensitive detail panel | Yes (keyboard: `Ctrl+I`) |
| Status Bar | 24px fixed | Ambient app state strip | No |

**The fundamental spatial law** (from Evil Martians research):
> Elements on the left control elements on the right. Elements on top control elements below.

The Navigator selects what appears in the Editor. The Editor's current state drives the Inspector. The Status Bar reflects the Editor's context. This is non-negotiable — no controls on the right that drive content on the left.

---

## 3. Zone Specifications

### 3.1 Activity Bar (40px, far left)

Icon-only column. 6 activities with keyboard shortcuts. Active activity = 1px neon-cyan left border on icon.

| Icon | Activity | Shortcut | Navigator Content |
|------|----------|----------|-------------------|
| `⬡` | Project / Files | `Ctrl+Shift+E` | Project tree (folders → prompts → forges) |
| `⏱` | Run History | `Ctrl+Shift+H` | Global optimization history (all runs) |
| `🔗` | Chains | `Ctrl+Shift+L` | Chain documents (multi-step prompt pipelines) |
| `📚` | Templates | `Ctrl+Shift+T` | Built-in and saved prompt templates |
| `⬡` | GitHub | `Ctrl+Shift+G` | Linked repos, context sources, codebase browser |
| `⚙` | Settings | `Ctrl+,` | App settings, provider config, API key |

Visual: 24px SVG icons, text-dim at rest, text-primary on hover (150ms), neon-cyan when active. No labels — icon meaning learned through use.

---

### 3.2 Navigator Panel (240px default)

The Navigator is a **single-purpose panel** whose content is fully determined by the active Activity. It is not a sidebar menu with sections — it is one thing at a time.

#### Navigator: Project / Files

A hierarchical tree using the PFFS (PSFS (Project Synthesis FileSystem)) model. Nodes:

```
▼ MyProject/
  ▼ prompts/
    📝 system-prompt.md         (draft, no forges)
    📝 user-query.md        ✦   (3 forges, latest: 8.1/10)
  ▼ chains/
    🔗 rag-pipeline.chain
  ▼ forges/
    🔥 user-query_v3.forge      (score: 8.1, chain-of-thought)
    🔥 user-query_v2.forge      (score: 7.4, co-star)
```

- File icons: 📝 prompt, 🔥 forge artifact, 🔗 chain, 📁 folder
- Score badge: `8.1` shown inline for forge artifacts (Geist Mono 9px, score-mapped color)
- Hover: Recipe A (border-accent shift + bg-hover/40, 200ms)
- Click: opens document in Editor Groups via a new tab
- Right-click: context menu (rename, delete, copy path, forge again)
- Keyboard: arrow keys navigate, Enter opens, F2 renames

#### Navigator: Run History (Global)

A dense table list of all optimization runs across all prompts, newest first.

Each row (40px height):
```
[strategy chip] [score circle] [prompt title, truncated] [relative time]
chain-of-thought  8.1/10       user-query.md              2h ago
```

- Sortable by: score, date, strategy, project
- Filterable by: strategy picker (multiselect chips), score range slider, project
- Multi-select via Shift+click → "Compare 2 runs" button appears in a contextual toolbar above the list
- Click: opens the forge artifact in Editor Groups as a read-only review tab

#### Navigator: Chains

List of chain documents. Each shows: chain name + number of steps + last run score.
Click: opens the Chain Composer in the Editor Groups center zone.

#### Navigator: GitHub

Tree view of linked repositories. Shows:
- `⬡ connected` / `⬡ not connected` status at top
- Per-linked repo: branch selector, file tree browser (read-only)
- Click file in tree: opens in Inspector as a context preview (not in Editor)
- "Link a repo" button at bottom when no repos linked

---

### 3.3 Editor Groups (center, fills)

The primary work surface. A **tabbed, splittable document editor** — the visual center of mass. Every other zone serves the Editor Groups.

#### Tab Bar

- Browser-style tabs, each showing: file icon + name + modified dot (●) if unsaved
- Max 8 tabs before LRU eviction (with a user-visible warning on tab 7)
- Keyboard: `Ctrl+Tab` (cycle), `Ctrl+W` (close), `Ctrl+Shift+T` (reopen closed), `Ctrl+[1-8]` (jump to tab N)
- Overflow: `>` button at right edge of tab bar opens a dropdown list of all open tabs
- Drag tabs to reorder
- Each tab carries: `{ document, sub-tab, scrollPosition, resultId, mode }`

#### Document View: Sub-tabs

Every open prompt document has three sub-tabs:

```
[Edit] [Pipeline] [History]
```

**[Edit] sub-tab** — the prompt authoring surface:
- Textarea: Geist 14px, bg-input, 1px border-subtle focus→neon-cyan
- Below textarea: Context Bar (injected `@` references as chips)
- Below context bar: Action row — [Forge `Ctrl+Enter`] [Strategy ▾] [Save `Ctrl+S`]
- Word count + character count: 10px Geist Mono text-dim, bottom-right of textarea
- `@` injection: typing `@` triggers a fuzzy palette of context sources (templates, knowledge sources, files). Selected sources appear as chips below the textarea. Chips show: icon + name + size.

**[Pipeline] sub-tab** — the Vertical Stage Track:
- Automatically becomes active when the user triggers Forge
- Shows the 4-5 stage pipeline as a vertical sequence (see Section 5)
- After forging completes, persists as a read-only trace of the last run
- Status summary bar at top: `Run 3 of 3 · 14.2s · 8.1/10 · 2,840 tokens`

**[History] sub-tab** — run history for THIS prompt only:
- Dense table: each row = one optimization run
- Columns: Run#, Strategy, Score, Delta, Duration, Tokens, Date
- Expandable rows: clicking a run expands inline to show the full pipeline trace (all stages, their output and metadata)
- "Compare" button appears in contextual toolbar when 2 rows are selected
- "Re-forge with same settings" action on hover

#### Document View: Forge Artifacts (`.forge` files)

When a `.forge` file is opened from the Navigator, the editor shows a **Review Layout** (not the Edit+Pipeline layout):

```
┌─── FORGE REVIEW ───────────────────────────────────────────────────┐
│ [Title] [Strategy badge] [Score circle] [Date]  [Re-forge] [Copy]  │
├───────────────────────────────────────────────────────────────────┤
│ [Optimized] [Diff] [Scores] [Trace]   ← sub-tabs                  │
│                                                                     │
│  < content for selected sub-tab >                                  │
└────────────────────────────────────────────────────────────────────┘
```

- **Optimized**: the final output text, copyable. Font: Geist 14px text-primary.
- **Diff**: side-by-side original (text-secondary) vs optimized (text-primary) with additions/deletions highlighted using neon-green/neon-red backgrounds (no glow).
- **Scores**: 5 dimension cards in 2-col grid, each with label + score circle + fill bar.
- **Trace**: the full Vertical Stage Track trace (collapsed by default, expandable per stage).

#### Split Editing

The Editor Groups support horizontal splits: `Ctrl+\` splits the current editor vertically (two documents side by side). Dragging a tab to the left or right edge of the editor area creates a split. This enables:
- Prompt on left, forge result on right
- Two prompt versions side by side for manual comparison
- Chain step on left, its output on right

---

### 3.4 Inspector Panel (280px default, right side)

The Inspector is **context-sensitive** — its content changes based on what is active in the Editor. It never requires the user to navigate to it directly; it exists as persistent ambient metadata.

| Editor State | Inspector Content |
|---|---|
| Edit sub-tab, cursor in textarea | Strategy Recommendations (top 3, with confidence bars) + Knowledge Sources |
| Forging in progress | Live Stage Detail (current stage output, streaming) |
| Pipeline sub-tab (post-forge) | Score Breakdown (5 dimensions) + Analysis summary |
| History sub-tab, no selection | Run statistics (avg score, most used strategy, best run) |
| History sub-tab, run selected | Full score breakdown for selected run |
| Forge artifact (Review) | Original prompt + Context Snapshot |
| Chain document open | Chain step list + per-step score history |

**Inspector sections** always use:
- Section headings: Syne 11px 700 uppercase letter-spacing-[0.08em] text-dim
- Data values: Geist Mono text-primary
- Labels: Geist 12px text-secondary
- Borders: 1px border-subtle between sections

---

### 3.5 Status Bar (24px, bottom)

A permanent 1-line strip. All fields are clickable to reveal detail or trigger an action.

```
│ ⬡ octocat/repo@main  │  chain-of-thought  │  8.2/10  │  CLI ●  │  Ctrl+K  │
```

| Field | Meaning | Click Action |
|---|---|---|
| `⬡ owner/repo@branch` | Linked GitHub repo (purple) | Open GitHub Navigator |
| Strategy name | Active strategy for current prompt | Open strategy picker |
| `8.2/10` | Score of latest forge for current prompt | Open score breakdown in Inspector |
| `CLI ●` | Active provider (green dot = healthy) | Open Settings |
| `Ctrl+K` | Palette shortcut reminder | Open Command Palette |
| Right side: `Analyzing... (Stage 1/4)` | Shows during active forge | Opens Pipeline sub-tab |

The Status Bar uses 10px Geist Mono for all values. Borders between fields: 1px border-subtle. Background: bg-secondary.

---

## 4. The Prompt Document Model

Every prompt in Project Synthesis is a **first-class document** with the following data model. This drives both the Navigator tree and the Editor's multi-sub-tab layout.

```
Prompt Document {
  id: string
  name: string                    // display name (editable in IDE panel)
  content: string                 // the raw prompt text
  version_label: string | null    // "v2", "prod", etc.
  tags: string[]
  project_id: string | null       // null = desktop (unorganized)

  runs: OptimizationRun[]         // all forge runs, newest first
  latest_run: OptimizationRun | null

  context: ContextSnapshot | null // most recently used codebase context
  chain_position: number | null   // set if this prompt is part of a chain
}

OptimizationRun {
  id: string
  created_at: datetime
  strategy: StrategyName          // one of the 10
  model_routing: ModelRouting     // which model ran each stage
  overall_score: number           // 1-10 integer
  score_delta: number | null      // vs previous run (+ or -)
  duration_ms: number
  total_tokens: number
  stages: StageTrace[]            // one per pipeline stage
  context_snapshot: ContextSnapshot | null
  raw_prompt: string              // the input
  optimized_prompt: string        // the output
}

StageTrace {
  stage: 'explore' | 'analyze' | 'strategy' | 'optimize' | 'validate'
  model: string
  duration_ms: number
  tokens: number
  output: string | object         // stage-specific structured output
  tool_calls: ToolCall[]          // for Stage 0 (Explore) only
}
```

**Why this model:** It mirrors the git model for code. The `Prompt` is the repository (the source of truth). Each `OptimizationRun` is a commit (a snapshot of execution with its full trace). The `StageTrace` array is the commit's diff set (what each stage produced). Engineers understand this model intuitively.

---

## 5. The Vertical Stage Track

The Stage Track is Project Synthesis's primary pipeline visualization. It replaces the node-graph canvas pattern (which is architecturally dishonest for a fixed-topology pipeline) with a vertical, sequential, streaming view.

### Why Not a Node Graph?

The Analyze→Strategy→Optimize→Validate pipeline is a **railroad, not a DAG**. Visualizing it as a node graph implies the user can add, remove, or reorder nodes — which they cannot. Using a node graph canvas here would be like using a flowchart to show a Python function's sequential lines. A vertical list is the honest representation of sequential execution.

Research confirms: LangSmith, the most production-hardened LLM tracing tool, uses a **nested waterfall** (hierarchical indented list + timing bars) rather than a graph for traces. Flowise/Langflow's node graphs work for user-assembled DAG workflows — not for fixed pipelines.

### Stage Track Layout

```
┌─── RUN 3 OF 3  ·  14.2s  ·  8.1/10  ·  2,840 tok ──────────────────────────┐
│                                                                               │
│  ●───┐  00 // EXPLORE                          SONNET    ✓ 12.1s  5 files    │
│      │     [collapsed: "Grounded in 5 files: schema.sql, main.py..."]        │
│      │                                                                        │
│  ●───┤  01 // ANALYZE                          HAIKU     ✓  2.3s  340 tok    │
│      │     Task: coding · Complexity: high                                   │
│      │     Weaknesses: lacks specificity, no output format defined           │
│      │                                                                        │
│  ●───┤  02 // STRATEGY                         OPUS      ✓  8.7s  1,240 tok  │
│      │     ▣ chain-of-thought  ·  confidence: 0.94                           │
│      │     "Selected for multi-step reasoning task with..."                  │
│      │                                                                        │
│  ●───┤  03 // OPTIMIZE                         OPUS      ✓ 18.3s  892 tok    │
│      │     [streaming output / final optimized text]                         │
│      │                                                                        │
│  ●───┘  04 // VALIDATE                         SONNET    ✓  4.1s  368 tok    │
│              [8.1/10 · clarity:8 · specificity:9 · structure:7 · ...]        │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Stage Track Behavior

**Before forge (Edit sub-tab active):** Stage track shows faint inactive state — stage names in text-dim, all circles empty, no duration/token data.

**On forge trigger (`Ctrl+Enter`):**
1. Editor automatically switches to Pipeline sub-tab (200ms slide-in-right animation)
2. Status Bar right-side shows "Forging... (Stage 1/4)"
3. Stage 00 (Explore, only if repo linked) or Stage 01 (Analyze) becomes active: spinner on stage circle, left-accent border intensifies from /30 to /100

**During active stage:**
- Circle: `border-t 2px solid stage-color, animation: spin 800ms linear infinite`
- Output region: tokens stream in via opacity 0→1 per chunk (30ms per chunk)
- Duration timer: counts up in 10px Geist Mono text-dim
- All subsequent stages: text-dim, empty circles, no content

**Stage completion:**
- Circle fills to `stage-color/20` with checkmark icon `stage-color`
- Duration + token count appear as badges at right of header
- Output region auto-collapses to single-line summary (name + first 80 chars)
- Next stage becomes active (slide-up-in animation 200ms)

**After full completion:**
- Stage 00 (Explore, if ran): shows "Grounded in N files" summary badge
- All stages collapsed by default, showing summary line
- Clicking any stage header expands full output (section-expand 300ms)
- Inspector panel updates to Score Breakdown view

### Stage-Specific Styling

| Stage | Left-Accent Color | Stage Badge Content |
|---|---|---|
| 00 Explore | `neon-purple` | Tool call activity feed (Geist Mono terminal style) |
| 01 Analyze | `neon-blue` | Task type chip + complexity badge + weaknesses/strengths |
| 02 Strategy | Active strategy's chromatic color | Strategy name badge + confidence bar |
| 03 Optimize | `neon-cyan` | Streaming optimized text (Geist 13px) |
| 04 Validate | Score-mapped color | 5 dimension bars + overall score circle |

**Model badges** (right of stage header, 9px Geist Mono chip-rect):
- `HAIKU` → neon-teal/60 border
- `SONNET` → neon-indigo/60 border
- `OPUS` → neon-purple/60 border

---

## 6. The `@` Context Injection System

Borrowed from Cursor's `@file` pattern and adapted for prompt engineering.

### How It Works

In the Edit sub-tab, the user types `@` anywhere in the prompt or in the context bar below the textarea. A fuzzy autocomplete popup (dropdown-enter animation, 200ms) appears with:

```
┌─── @ Context Sources ──────────────────────────────────────┐
│  🔍 fuzzy search...                                         │
├──────────────────────────────────────────────────────────── │
│  📝 PROMPTS          user-query.md, system-prompt.md        │
│  📚 KNOWLEDGE        schema.sql (4.2kb), API docs (1.1kb)  │
│  🔗 FILES            (requires linked repo)                 │
│  📋 TEMPLATES        Chain-of-Thought template, RISEN...    │
└─────────────────────────────────────────────────────────────┘
```

Selecting a source adds it as a **Context Chip** in the Context Bar below the textarea:

```
Context: [@schema.sql 4.2kb ×] [@API conventions 1.1kb ×] [+ Add]
```

Each chip:
- Border: 1px neon-teal/40 | Background: neon-teal/8 | Text: neon-teal 10px Geist Mono
- `×` dismiss: hover → neon-red (Recipe D, 150ms)
- Chips passed as `codebase_context.sources` payload to the pipeline

### Why This Over the Form Panel Approach

The prior version had a "Context Profile" form panel with fields for language, framework, description, etc. This required deliberate navigation and filling in freeform text. The `@` system:
- Requires zero additional navigation (it's triggered in-place while composing)
- References actual file content (not a manual description of it)
- Is visually checkable (the chip shows exactly what's included)
- Can compose (stack multiple sources)
- Teaches the pattern through familiar UX (Slack @mention, Cursor @file)

---

## 7. The Chain Composer

For multi-step prompt pipelines (prompt A's output becomes prompt B's input), Project Synthesis provides a **Chain Composer** — a sequential visual editor that is NOT a node graph canvas.

### Why Not a Node Graph

Research finding: node graphs (React Flow canvas) are appropriate for user-assembled, branching DAGs where the user defines the topology. Prompt chains in 90% of real use cases are strictly sequential with no branching. A node graph for a sequential list adds spatial cognitive load and implies configurability that doesn't exist.

The Chain Composer uses a **vertical card stack with arrow connectors** — the linear chain pattern from PromptLayer's chainer, not the canvas pattern from Langflow.

### Chain Document Layout

```
┌─── RAG Pipeline ──────────────────────────────────────────────────── [Run Chain] ┐
│                                                                                    │
│  ┌── Step 1: Retrieval Query ─────────────────────────────────────────────────┐  │
│  │  [prompt.md]  strategy: auto  [Edit] [Forge isolated]    score: 8.1/10    │  │
│  └─────────────────────────────────────────────────────────────────────────────┘  │
│                          ▼  output → input                                        │
│  ┌── Step 2: Context Synthesis ───────────────────────────────────────────────┐  │
│  │  [synthesis.md]  strategy: auto  [Edit] [Forge isolated]  score: 7.9/10   │  │
│  └─────────────────────────────────────────────────────────────────────────────┘  │
│                          ▼  output → input                                        │
│  ┌── Step 3: Final Response ──────────────────────────────────────────────────┐  │
│  │  [response.md]  strategy: auto  [Edit] [Forge isolated]  score: 8.4/10    │  │
│  └─────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                    │
│  [+ Add Step]  [Run Full Chain]                                  Total: 8.1 avg  │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Chain Interactions

- **"Forge isolated"**: runs the full pipeline (Analyze→Strategy→Optimize→Validate) on a single chain step in isolation, opening that step's Pipeline sub-tab in the Editor.
- **"Run Full Chain"**: runs each step sequentially, passing the `optimized_prompt` of step N as a prepended user message to step N+1.
- Expanding a step card shows its pipeline history (compact run table)
- Arrow connectors animate as data flows during chain execution (neon-cyan line filling downward, 300ms per connector)
- "Add Step" appends a new step card (fade-in 400ms)

---

## 8. Command Palette

`Ctrl+K` (or `Cmd+K` on macOS) opens the universal command palette from anywhere in the app.

### Palette Behavior

- **Open**: immediately, <100ms from keypress. dialog-in animation (300ms spring).
- **Initial state** (no query): shows contextual suggestions based on current Editor state
- **Query**: fuzzy search across all commands, recent documents, and settings
- **Navigation**: arrow keys, Enter to execute, Escape to dismiss, Tab to autocomplete
- **Prefix scoping**:
  - `@` — search context sources (same as the `@` injection in Edit tab)
  - `>` — force command mode (hides file results)
  - `#` — search within history

### Contextual Commands (shown without query, based on current state)

**Always present:**
- `Forge  Ctrl+Enter` → triggers pipeline on active prompt
- `New Prompt  Ctrl+N` → creates a blank prompt in current project
- `Open History  Ctrl+H` → switches Navigator to History activity

**When a prompt is open and has runs:**
- `Re-forge  Ctrl+Shift+R` → same prompt, same settings
- `Compare Last Two Runs` → opens diff view in Editor
- `Change Strategy…` → submenu: 10 strategies with color badges

**When a forge artifact is active:**
- `Copy Optimized Text  Ctrl+C` → copy with copy-flash animation
- `Save as New Prompt` → creates a new prompt from the optimized output
- `Open Original Prompt` → opens the source prompt document

**When text is selected in the textarea:**
- `Forge Selection Only` → runs pipeline on just the selected text

### Shortcut Education

Every command in the palette shows its keyboard shortcut at the right edge of the row. This is the pedagogical mechanism: users discover shortcuts through the palette, then begin using them directly.

---

## 9. Navigation and Keyboard Model

### Keyboard Map (Primary Actions)

| Action | Shortcut |
|---|---|
| Command Palette | `Ctrl+K` |
| Forge current prompt | `Ctrl+Enter` |
| New prompt | `Ctrl+N` |
| Save | `Ctrl+S` |
| Close tab | `Ctrl+W` |
| Next tab | `Ctrl+Tab` |
| Previous tab | `Ctrl+Shift+Tab` |
| Split editor | `Ctrl+\` |
| Toggle Navigator | `Ctrl+B` |
| Toggle Inspector | `Ctrl+I` |
| Toggle Pipeline sub-tab | `Ctrl+P` |
| Toggle History sub-tab | `Ctrl+Shift+H` |
| Copy result | `Ctrl+Shift+C` |
| Compare mode | `Ctrl+D` |
| Re-forge | `Ctrl+Shift+R` |
| Cancel active forge | `Escape` |

### Focus Model

Tab order follows the left-to-right spatial convention:
1. Activity Bar icons
2. Navigator tree/list
3. Editor Groups (textarea or document)
4. Inspector panel

`Ctrl+[1-4]` jump directly to each zone.

### No Modal Dialogs for Configuration

Configuration dialogs (Prompt rename, Settings changes, Strategy selection) appear as:
- **Command palette sub-steps** (for single-value choices)
- **Inspector panel updates** (for metadata editing inline)
- **Inline forms in the document** (for context chips, version label)

Modals are reserved for destructive confirmations only: `Delete prompt?` / `Reset history?` These are the ONLY modals in the app.

---

## 10. Data Hierarchy and Navigation Model

The information architecture mirrors git:

```
Workspace (global)
  └── Project (a folder / repository)
        └── Prompt (a .md file — the document)
              └── OptimizationRun (a commit — one forge execution)
                    └── StageTrace (the commit's internal steps)
                          └── Token (inspectable in raw view)
```

Navigation moves down on click, back up via breadcrumbs in the Editor's title bar:

```
MyProject / prompts / user-query.md / Run 3
```

Each breadcrumb is clickable to navigate back. The breadcrumb is the only "you are here" indicator the app needs — no separate heading or page title.

---

## 11. State Persistence Rules

| State Type | Storage | Persistence |
|---|---|---|
| Open tabs + active tab | `sessionStorage` | Per browser session |
| Panel widths (Navigator, Inspector) | `localStorage` | Permanent |
| Active Activity (which icon selected) | `sessionStorage` | Per session |
| Navigator scroll position per activity | `sessionStorage` | Per session |
| Editor scroll position per tab | `sessionStorage` | Per session |
| Pipeline sub-tab forging state | In-memory only | Cleared on page refresh |
| History sort/filter preferences | `localStorage` | Permanent |
| Command palette recent items | `localStorage` | Permanent |

**Golden rule:** Panel geometry (widths) persists forever. Content state (what's open, where you are) persists per session. In-progress work (active forge) is ephemeral.

---

## 12. IDE Integration Mode

Project Synthesis can be invoked from VS Code, Cursor, or any IDE via:
1. **Browser shortcut** → opens a new prompt in the default project, pre-filled with selected text from clipboard
2. **VS Code Extension** (future) → right-click menu "Optimize with Project Synthesis" → opens in a VS Code WebView panel using the Workbench layout
3. **CLI** (future) → `forge optimize --file prompt.md` → runs the pipeline headlessly, writes result to `prompt.forge.md`

The key design requirement: **the Workbench layout works inside a WebView panel** without modification. The Activity Bar + Navigator + Editor + Inspector pattern compresses gracefully to a 480px minimum width because:
- Activity Bar: 40px (fixed, always visible)
- Navigator: collapses to 0px when narrow (`Ctrl+B`)
- Inspector: collapses to 0px when narrow (`Ctrl+I`)
- Editor Groups: fills all remaining space

At 480px width, the user sees only the Activity Bar icons + Editor — a clean, focused prompt editing surface with the full pipeline track available on the Pipeline sub-tab.

---

## 13. Anti-Patterns

These are banned at the architectural level. If a PR introduces any of these, it must be revised.

| Anti-Pattern | Why Banned | Correct Alternative |
|---|---|---|
| **Welcome carousel / onboarding modal** | Patronizing to developers. Assumes incompetence. | Empty states show the data model structure (table headers, tree chrome) + keyboard shortcut hint |
| **Hero section / marketing copy in app** | Wrong audience. Engineers want tools, not pitches. | The first thing users see is the Editor with a blank prompt and the command palette hint |
| **Node graph canvas for the fixed pipeline** | Architecturally dishonest. Implies user-configurable topology. | Vertical Stage Track |
| **Full-screen spinner for pipeline execution** | Blocks the entire UI. User cannot browse history while waiting. | Pipeline progress in Status Bar; Editor tab shows spinner on tab; user can open other tabs |
| **Confetti / celebration animation on forge completion** | Consumer-grade. | Stage circle fills neon-green, score badge appears. Sufficient. |
| **Modal dialogs for configuration** | Blocking. Doesn't match keyboard-first workflow. | Inline forms, Inspector panel, Command Palette sub-steps |
| **Flat, unstructured history list** | No information at the card level. Forces clicks to discover data. | Enriched Run Cards with strategy badge, score circle, first-line preview |
| **Side-by-side as optional "compare mode"** | Comparison should be the default for evaluation, not hidden behind a toggle | "Compare" action on multi-select is always one click away |
| **Form panel for codebase context** | Slow, navigational, disconnected from writing flow | `@` injection chips in Edit sub-tab |
| **"Loading..." text without skeleton state** | Flash of nothingness is worse than a spinner | Always render layout skeleton first, fill with data |
| **Single global history view as the only way to see runs** | Disconnects runs from the prompt that generated them | Per-prompt History sub-tab is the primary history surface |

---

## 14. Component Vocabulary

Canonical names used in code, comments, and issues. Do not invent synonyms.

| Component Name | Description |
|---|---|
| `Workbench` | The root layout: Activity Bar + Navigator + Editor Groups + Inspector + Status Bar |
| `ActivityBar` | Far-left icon column (40px) |
| `Navigator` | Left panel, content determined by active activity |
| `EditorGroups` | Center tabbed document area |
| `EditorTab` | One open document (prompt, forge artifact, chain) |
| `SubTabs` | [Edit] [Pipeline] [History] within an EditorTab |
| `StageTrack` | Vertical sequential pipeline visualization |
| `StageCard` | One stage in the Stage Track (header + content + metadata) |
| `Inspector` | Right context-sensitive panel |
| `StatusBar` | Bottom ambient state strip |
| `CommandPalette` | Cmd+K fuzzy action launcher |
| `ContextChip` | `@`-injected source reference badge in the Context Bar |
| `ContextBar` | Row of context chips below the textarea in Edit sub-tab |
| `RunCard` | Enriched list item in History (Navigator or sub-tab) |
| `ChainComposer` | Sequential card stack for multi-step prompt pipelines |
| `ChainStep` | One prompt card within the Chain Composer |
| `ForgeReview` | Read-only layout for opening `.forge` artifact files |
| `ScoreCircle` | 20px colored circle with score integer (Geist Mono 10px) |
| `ModelBadge` | Chip showing which Claude model ran a stage (HAIKU/SONNET/OPUS) |
| `StrategyBadge` | Chip showing strategy name with strategy's chromatic color |
| `DiffView` | Side-by-side or inline diff (original vs optimized) |
| `SkeletonState` | Placeholder layout shown during data loading |

---

## 15. Relationship to Brand Guidelines

This architecture is the spatial expression of the brand. Cross-references:

- **Zero-Effects Directive** → no glow on Stage Track cards, no drop-shadows on panels, all contours are 1px solid borders
- **Chromatic Encoding** → Stage Track left-accent colors are semantic (see Section 5); strategy badges use exact strategy→color mapping; score circles use exact score→color mapping
- **5-State Interaction Machine** → all interactive elements (tabs, Navigator items, stage headers, buttons) follow the Resting→Hover→Active→Focus→Disabled lifecycle
- **forge-spark animation** → fires on the Forge button (`Ctrl+Enter`) at the moment of click, before the pipeline begins
- **Ultra-compact density** → Navigator row height 40px, Status Bar 24px, Stage Track header 32px. VS Code density target throughout.
- **Geist Mono for data** → all scores, timestamps, token counts, duration values, strategy badges, model badges, breadcrumb separators
- **Syne for headings** → Activity Bar tooltips, section headings in Navigator, Stage Track labels ("00 // EXPLORE"), Inspector section headings
- **Spring entrance bezier** → all panel reveals, sub-tab switches, stage expansions use `cubic-bezier(0.16, 1, 0.3, 1)`
- **Accelerating exit bezier** → all panel closes, stage collapses use `cubic-bezier(0.4, 0, 1, 1)`

---

## 16. Empty States and First-Launch Experience

Empty states are not decoration — they are the primary data model teacher for new users. Every empty state must answer: "What goes here?" and "How do I get there?"

### Canonical Empty State Structure

All empty states follow this 4-tier structure (in order, top to bottom):

1. **Icon** — 24px SVG at 30% opacity. Must be semantically linked to the content type (a prompt icon for an empty prompt list, not a generic folder). Never a mascot, illustration, or large graphic.
2. **Primary message** — 12–13px `text-primary` — factual statement of what is empty: `"No forges yet"`. One sentence, no punctuation unless a question.
3. **Secondary message** — 11px `text-dim` — the next action: `"Forge a prompt to see results here"`. Instructional, not emotional.
4. **CTA button** — optional. Only include if the user can directly take action from this screen. Omit if the action requires prior steps the UI cannot shortcut. Use `btn-primary`.

**What to never do:** Mascots, hero illustrations, paragraphs of explanatory text, "Get started!" CTAs without specific instruction, or cheerful taglines. This is a developer tool.

### Per-Zone Empty States

**Navigator / Files (no prompts in project):**
```
[📝 icon, 30% opacity]
No prompts
Create a prompt with Ctrl+N or drag a .md file here
[+ New Prompt]
```

**Navigator / History (no forge runs yet):**
```
[🔥 icon, 30% opacity]
No forges yet
Forge your first prompt with Ctrl+Enter
```
No CTA — the action must happen in the Editor, not the Navigator.

**History sub-tab for a prompt with no runs:**
```
[🔥 icon, 30% opacity]
This prompt has never been forged
Press Ctrl+Enter to run the optimization pipeline
```

**Inspector (nothing open in Editor):**
```
[⬡ icon, 30% opacity]
Open a prompt to see metadata
```
Single line only. The Inspector is ambient — it does not need instructional text.

**Search / filter with no results (Navigator History or History sub-tab):**
```
No forges matching "{query}"
Clear search    (inline text link, not a button)
```
Reflect the search query back to the user.

### First-Launch Experience

On first launch, the Editor shows a **blank prompt document** pre-loaded with a placeholder:

```
# My First Prompt

Describe the task you want to optimize...
```

Below the textarea, the Context Bar is empty with a ghost chip: `+ Add context with @`. The Forge button is present and enabled. A single keyboard shortcut hint appears in the Status Bar: `Ctrl+Enter to forge · Ctrl+K for all commands`.

**No onboarding modal. No welcome carousel. No tour highlights.** Engineers read by doing. The first blank prompt IS the tutorial.

---

## 17. Pipeline State Taxonomy and Error Visualization

### The 7-State Taxonomy

Based on industry standard (GitHub Actions, CircleCI, PatternFly): every pipeline stage and overall run must support exactly 7 states with distinct visual treatments. Never conflate failure modes.

| State | Semantic | Color | Stage Circle Visual |
|-------|----------|-------|---------------------|
| `pending` | Not yet started | `text-dim` (#7a7a9e) | Hollow ring, 1px border-dim |
| `running` | Currently executing | `neon-cyan` | Spinning top-border, 2px, 800ms linear |
| `success` | Completed without error | `neon-green` | Filled circle/20 + checkmark |
| `failed` | LLM returned bad data / non-zero exit | `neon-red` | Filled circle/20 + × icon |
| `cancelled` | User stopped before completion | `text-secondary` (#8b8ba8) | Hollow circle + slash (⊘) icon |
| `timed_out` | Hit wall-clock limit (distinct from failed) | `neon-orange` | Filled circle/20 + clock+× icon |
| `skipped` | Skipped due to prior stage failure | `text-dim` | Hollow circle + → icon, entire stage row dimmed to 40% opacity |

**Critical distinction:** `failed`, `cancelled`, and `timed_out` are three different failure modes requiring different user responses. `failed` = investigate the LLM output or context. `cancelled` = user action, no investigation needed. `timed_out` = increase timeout or reduce context size. Using the same red for all three makes the history table useless for diagnosis.

### Overall Pipeline Status

The run header status (in the Stage Track run summary bar) reflects the **most critical child status**:
- Any child `failed` → run shows `failed`
- Any child `timed_out` → run shows `timed_out` (if no failed stages)
- Any child `cancelled` → run shows `cancelled` (if no failed or timed_out stages)
- All children `success` → run shows `success`
- Mix of `success` + `skipped` → run shows `success` with a dim badge `(2 stages skipped)`

Prior stages that succeeded retain their `neon-green` circles even when subsequent stages fail. Visual continuity: the completed work is still valid.

### Partial Success — Pipeline Progress Encoding

When a run ends with mixed results, the stage connector lines encode the transition:

```
● ──── 01 ANALYZE     ✓ (neon-green)
│
● ──── 02 STRATEGY    ✓ (neon-green)
│
● ──── 03 OPTIMIZE    ✗ (neon-red) ← connector above this is red
│
◦ ──── 04 VALIDATE    skipped      ← connector is dim, stage 40% opacity
```

The inter-stage connector adopts the destination stage's status color. Connectors above successful stages are `neon-green/30`. The connector leading to the failed stage is `neon-red/50`. Connectors after the failure point are `border-subtle`.

### Inline Error Annotation

When a stage fails, the **first 80 characters of the error message appear inline** in the stage header row — the user should never need to expand a stage to see _what_ went wrong:

```
● ─── 03 // OPTIMIZE     OPUS     ✗  8.7s    JSON parse error: unexpected token at pos 247…
```

The error text is `neon-red/70`, Geist Mono 10px, truncated with `…`. Clicking the stage expands the full error with stack details and a "Retry this stage" action.

### GitHub-Actions-Style Inline Failure Annotation

For the HistoryWindow run table, when a run has a failed stage, show the stage name + error summary in a dim second line below the run title:

```
user-query.md · chain-of-thought              8.7s  [failed]
  └ Stage 03: JSON parse error at pos 247
```

The sub-line is `text-dim` 10px Geist Mono. This eliminates the need to open the run to diagnose what went wrong.

---

## 18. Accessibility

Accessibility in Project Synthesis is non-negotiable and must meet WCAG 2.1 AA. This section defines the exact requirements for the workbench layout.

### ARIA Landmark Structure

The 5-zone workbench maps to standard ARIA landmarks with unique labels:

```html
<nav aria-label="Activity Bar">           <!-- Activity Bar: icon-only -->
<nav aria-label="Navigator">              <!-- Navigator panel -->
<main aria-label="Editor">                <!-- Editor Groups -->
<aside aria-label="Inspector">            <!-- Inspector panel -->
<footer aria-label="Status Bar">          <!-- Status Bar -->
```

Each landmark must have a **unique** `aria-label` — having two `<nav>` elements without distinct labels is an ARIA conformance failure.

Within `<main>`, the EditorGroups tab bar uses:

```html
<div role="tablist" aria-label="Open documents">
  <button role="tab" aria-selected="true" aria-controls="panel-1">prompt.md</button>
  <div role="tabpanel" id="panel-1">...</div>
</div>
```

### Two-Tier Keyboard Zone Navigation (F6 Model)

Based on VS Code's accessibility model, the workbench uses a **two-tier navigation system**:

**Tier 1 (Coarse — Zone Cycling):**
- `F6` cycles focus forward through the 5 ARIA landmark zones: Activity Bar → Navigator → Editor → Inspector → Status Bar → (wrap)
- `Shift+F6` cycles backward
- The currently focused zone receives a faint neon-cyan outline on the zone's outer border (1px, 30% opacity) — distinct from element-level focus rings

**Tier 2 (Fine — Within Zone):**
- Once a zone has focus, standard keyboard interaction applies: `Tab`/`Shift+Tab` for focusable elements, arrow keys for lists and trees, `Enter` to activate

**Rationale:** Relying solely on `Tab` to reach the Inspector from the textarea would require ~40+ tab stops. `F6` provides immediate zone jumping without disturbing the tab order within zones.

### Roving Tabindex for Tab Bar

The EditorGroups tab bar uses **roving tabindex** (not individual `tabindex` on each tab):

```javascript
// Only the active tab has tabindex="0"; all others have tabindex="-1"
// Arrow keys move focus between tabs by updating tabindex
// Enter activates the focused tab
```

This makes the tab bar feel like a single focusable unit in the document's tab order, with arrow-key navigation within.

### Command Palette Focus Trap

The Command Palette requires a complete focus trap when open:

```html
<div role="dialog" aria-modal="true" aria-label="Command Palette">
  <input type="text" aria-label="Search commands" autofocus />
  <ul role="listbox" aria-label="Results">...</ul>
</div>
```

Implementation:
1. On open: store the previously focused element (`document.activeElement`)
2. Set focus to the search `<input>` immediately
3. Trap `Tab` / `Shift+Tab` to cycle only within the dialog's focusable children
4. `Escape` closes the palette and restores focus to the stored element
5. Clicking the backdrop also closes and restores focus

### WCAG AA Compliance — Color Contrast

**Known failure:** `text-dim` (#7a7a9e) achieves approximately **3.2:1 contrast** against `bg-primary` (#06060c). This fails WCAG AA (4.5:1 minimum for normal text).

**Rule:** `text-dim` may only be used for:
- Text at 18px or larger (AA large text threshold: 3:1)
- Decorative or non-informative text
- Placeholder text (WCAG exempts placeholders from contrast requirements)
- Timestamps, metadata, badge details — where `text-secondary` (#8b8ba8) would feel too prominent

**Never use `text-dim` for:** body copy, labels, interactive element text, error messages, or any text the user needs to read to perform a task.

`text-secondary` (#8b8ba8) achieves approximately **5.4:1** against `bg-primary` — passes AA. Use it instead of `text-dim` for readable secondary information.

### Prefers-Reduced-Motion (Three-Tier Model)

```css
@media (prefers-reduced-motion: reduce) {
  /* Tier 1 — Always safe (keep): opacity transitions, color changes */
  /* These are non-spatial and carry no vestibular risk */

  /* Tier 2 — Replace: spatial translations, scale, rotation */
  /* Replace: transform-based entrances with opacity-only fade */
  .stage-card { animation: fade-in 200ms ease; }   /* not slide-up-in */

  /* Tier 3 — Static alternative: skeleton shimmer, infinite loops */
  /* Skeleton: remove animation, use static bg-hover color */
  .skeleton { animation: none; background: var(--color-bg-hover); }
  /* Streaming cursor: remove blink, keep visible as static bar */
  .streaming-cursor { animation: none; opacity: 1; }
}
```

All keyframe animations must set `animation-duration: 0.01ms` as a fallback via the media query for the worst-case (older browsers that support `prefers-reduced-motion` but not all animation control).

### Screen Reader Accessible Labels

| Element | Label Strategy |
|---|---|
| Score circle | `aria-label="Score: 8.1 out of 10"` |
| Strategy badge | `aria-label="Strategy: Chain of Thought"` |
| Model badge | `aria-label="Model: Claude Haiku"` |
| Stage status icon | `aria-label="Status: complete"` (not just the checkmark SVG) |
| Progress spinner | `role="status" aria-live="polite" aria-label="Forging in progress"` |
| Context chip | `aria-label="Context: schema.sql, 4.2 kilobytes. Press Delete to remove."` |

Live regions: SSE-streamed stage content uses `aria-live="polite"` on the streaming content div. **Not `aria-live="assertive"`** — assertive would interrupt the user mid-action.

---

## 19. Responsive Breakpoints and CSS Grid Layout

### CSS Grid as the Workbench Shell

The workbench is implemented as a single CSS Grid on the `<body>` or root container. Panel widths are CSS custom properties mutated via JavaScript (for resize interactions), not inline styles:

```css
.workbench {
  display: grid;
  grid-template-columns:
    40px                           /* Activity Bar — fixed */
    var(--nav-width, 240px)        /* Navigator — resizable */
    1fr                            /* Editor Groups — fills */
    var(--inspector-width, 280px); /* Inspector — resizable */
  grid-template-rows: 1fr 24px;   /* Content + Status Bar */
  height: 100dvh;
  overflow: hidden;
}
```

**Resizing:** Panel resize handles set `--nav-width` and `--inspector-width` via:
```javascript
document.documentElement.style.setProperty('--nav-width', `${newWidth}px`);
```

This approach avoids re-layout thrashing because CSS custom property changes trigger a single recalculation, not forced-layout cycles.

**Collapse:** Toggling Navigator visibility sets `--nav-width: 0px` with `transition: --nav-width 150ms ease`. The panel's content has `overflow: hidden` so it vanishes cleanly.

### Breakpoint System

| Breakpoint | Width | Layout Mode | Behavior |
|---|---|---|---|
| **Full** | ≥ 1280px | 5-zone | All zones visible. Default column widths. Navigator resize + Inspector resize available. |
| **Compact** | 768px–1279px | 3-zone | Inspector auto-collapses (hides). Navigator may be narrowed to 200px. Inspector accessible via `Ctrl+I` as a flyout overlay. |
| **Narrow** | 480px–767px | Editor-only | Navigator auto-collapses. Inspector collapses. Activity Bar remains at 40px. Navigator accessible via `Ctrl+B` as a full-height drawer overlay. |
| **Minimal** | < 480px | Single column | Activity Bar becomes a bottom tab bar (4 icons). Navigator + Inspector are full-screen overlay drawers. Editor fills the screen. |

**Collapse rules (automatic, no user action):**
- Width drops below 1024px: Inspector collapses (`--inspector-width: 0`)
- Width drops below 768px: Navigator collapses (`--nav-width: 0`)
- Width drops below 480px: Activity Bar moves to bottom; all other geometry recalculated

**Flyout overlay (Compact mode):** When the user opens the Navigator via `Ctrl+B` in Compact mode, it renders as an `absolute`-positioned panel over the Editor, not a grid column. It has a backdrop (`bg-primary/60`) and closes on `Escape` or backdrop click. This prevents the Editor from shrinking on narrow screens.

### WebView / Embedded Mode

When embedded in a VS Code WebView panel (typically 400–600px width), the Narrow breakpoint applies automatically. The user sees: 40px Activity Bar + full-width Editor. The Navigator is accessible via `Ctrl+B` as a flyout. The Inspector is accessible via `Ctrl+I` as a flyout. The full pipeline track is on the Pipeline sub-tab.

This is the minimum viable workbench surface — still fully functional.

### Resize Constraints

| Panel | Minimum | Maximum |
|---|---|---|
| Navigator | 160px | 480px |
| Inspector | 200px | 400px |
| Editor Groups | 300px | (fills remaining) |

If a resize would push the Editor below 300px, it is rejected (the resize handle stops moving).

---

## 20. Animation Choreography

### The Forge Trigger Sequence

Exact timing for the forge action from button press to first streaming token:

```
t=0ms    User presses Ctrl+Enter / clicks Forge button
         ├─ forge-spark fires on button (instant, 300ms duration, plays out)
         ├─ API POST /optimize fires (async, non-blocking)
         └─ Edit sub-tab panel begins collapsing (150ms ease-out)
              using: grid-template-rows: 1fr → 0fr

t=150ms  Edit panel fully collapsed
         └─ Pipeline sub-tab becomes active (slide-in-right, 200ms spring)
              Status Bar shows: "Forging... · Stage 0/4 · Ctrl+. to cancel"

t=200ms  Stage 00 or Stage 01 becomes active (spring expand, 200ms)
         └─ Stage circle switches to spinning state (spin 800ms linear infinite)

--- [ LLM processing time: 200ms–2000ms depending on stage ] ---

t+N      First SSE token arrives
         └─ Token appears in streaming content area (RAF-batched, see Section 23)

t+M      Stage N completes (SSE `stage_complete` event)
         ├─ Circle icon swaps to checkmark (instant, 0ms)
         ├─ Circle border transitions to neon-green (80ms ease)
         ├─ Duration + token badges fade in (150ms ease)
         └─ Stage body begins collapsing (200ms ease-out, starts at t+80ms)
              ← 50ms before body fully collapsed, next stage begins expanding
                 This 50ms overlap creates a smooth handoff, not a gap

t+M+130ms  Next stage begins expanding (200ms spring)
           └─ 50ms overlap: previous stage still collapsing
```

**The 50ms overlap rule:** The next stage's expand animation begins 50ms before the current stage's collapse completes. This prevents a perceptible "dead zone" between stages that makes the pipeline feel laggy.

### Stage Collapse / Expand — CSS Implementation

```css
/* Stage card body wrapper */
.stage-body {
  display: grid;
  grid-template-rows: 1fr;       /* expanded */
  transition: grid-template-rows 200ms cubic-bezier(0.4, 0, 1, 1);
}

.stage-body.collapsed {
  grid-template-rows: 0fr;       /* collapsed */
}

/* Inner content must have min-height: 0 — required for grid-template-rows trick */
.stage-body-inner {
  min-height: 0;
  overflow: hidden;
}
```

This `grid-template-rows: 1fr → 0fr` technique animates height without `max-height` (which requires knowing the content height in advance) and without JavaScript height measurement.

### Auto-Scroll State Machine

For streaming content in the Stage Track and any scrollable output panel:

```typescript
type ScrollMode = 'auto' | 'locked';

class AutoScrollController {
  private mode: ScrollMode = 'auto';
  private container: HTMLElement;
  private anchor: HTMLElement;   // bottom sentinel element
  private observer: IntersectionObserver;

  constructor(container: HTMLElement, anchor: HTMLElement) {
    this.observer = new IntersectionObserver(
      ([entry]) => {
        // If anchor is visible, re-engage auto-scroll
        if (entry.isIntersecting && this.mode === 'locked') {
          this.mode = 'auto';
        }
      },
      { threshold: 0.5 }
    );
    this.observer.observe(anchor);

    container.addEventListener('wheel', () => {
      // Scroll-up gesture locks auto-scroll
      if (container.scrollTop < container.scrollHeight - container.clientHeight - 50) {
        this.mode = 'locked';
      }
    });
  }

  onNewContent(): void {
    if (this.mode === 'auto') {
      this.anchor.scrollIntoView({ behavior: 'instant' });
    }
    // If locked: "Jump to bottom" button is visible (managed separately)
  }
}
```

**"Jump to bottom" button:** Appears in the bottom-right corner of the scrollable area when `mode === 'locked'`. Neon-cyan, `btn-outline-primary` style, 28px height, `↓ Bottom` label. Clicking: resumes auto-scroll (`mode = 'auto'`) + scrolls to anchor + hides the button.

### Perceptual Timing Thresholds

These thresholds drive UX decisions about when to show loading states, intermediate feedback, and status messages:

| Threshold | Duration | Implication |
|---|---|---|
| **Instant** | < 100ms | No feedback needed. UI transitions within this window feel instantaneous. |
| **In-flow** | 100ms–400ms | Micro-loading acceptable. A progress bar on a button is appropriate. No spinner. |
| **Doherty threshold** | 400ms | Beyond this, users perceive a wait. Show a status indicator in the Status Bar. |
| **Tolerance** | 1s | Beyond this, users start to doubt the system is working. Stage timer must be counting up and visible. |
| **LLM TTFT** | < 700ms critical | Time to first token from the optimize stage. If TTFT exceeds 700ms with no visual feedback, users assume the request failed. Streaming the analysis stage text first (even if brief) covers this gap. |

**How Stage 01 (Analyze) covers LLM TTFT:** The Analyze stage is intentionally first because it uses `claude-haiku-4-5` (fast, cheap). It produces visible streaming output (task type, complexity analysis) within 300–500ms. This creates the perception that "the system is working" before the slower Opus stages begin.

### Animation Reference Table (Complete)

Extending the brand-guidelines keyframe table with workbench-specific additions:

| Animation | Duration | Easing | Trigger |
|---|---|---|---|
| Stage body collapse | 200ms | `cubic-bezier(0.4, 0, 1, 1)` | Stage completion |
| Stage body expand | 200ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Stage becoming active |
| Navigator collapse | 150ms | `ease` | `Ctrl+B` or breakpoint |
| Inspector collapse | 150ms | `ease` | `Ctrl+I` or breakpoint |
| Status dot color shift | 300ms | `ease-out` | State change |
| Score badge tint fade-in | 200ms | `ease-out` | Score available |
| Error shake | 400ms | `cubic-bezier(0.36, 0.07, 0.19, 0.97)` | Stage failure |
| "Jump to bottom" fade-in | 200ms | `ease` | Scroll locked |
| "Jump to bottom" fade-out | 200ms | `ease` | Scroll resumed |
| Skeleton shimmer | 1500ms | `ease-in-out, infinite` | Loading state |
| Row stagger entrance | 350ms + 40ms×n | `cubic-bezier(0.16, 1, 0.3, 1)` | History list load |

**Error shake implementation:** Applied to the Stage Track container when any stage fails. A horizontal shake communicates "impact":

```css
@keyframes error-shake {
  0%, 100% { transform: translateX(0); }
  15% { transform: translateX(-4px); }
  30% { transform: translateX(4px); }
  45% { transform: translateX(-3px); }
  60% { transform: translateX(3px); }
  75% { transform: translateX(-2px); }
  90% { transform: translateX(1px); }
}
```

The shake fires once (forwards fill mode) and is suppressed under `prefers-reduced-motion`.

---

## 21. Dense Table & History UX

### Row Height and Density Conventions

Based on industry research (W&B, DataGrip, TablePlus, Braintrust):

| Mode | Row Height | Use Case |
|---|---|---|
| Compact (default) | 32px | Navigator History list, all main tables |
| Default | 40px | History sub-tab expanded row details |
| Comfortable | 48px | Stage trace expanded view with multi-line content |

Project Synthesis uses **compact 32px rows** throughout history surfaces. This allows ~20 runs visible in a standard viewport without scrolling — matching the "maximum data density" principle.

All numeric values in table cells use `font-variant-numeric: tabular-nums` (available in Geist Mono) so that score values align vertically across rows regardless of digit count.

### Column Sorting

Sortable column headers follow the settled industry convention:

- **Unsorted, sortable:** show `⇅` icon at 40% opacity, appearing on `hover`
- **Sorted ascending:** show `▲` in the active accent color, full opacity
- **Sorted descending:** show `▼` in the active accent color, full opacity
- **Unsortable:** no icon (absence is the signal)
- **Icon position:** always to the **right** of the label text
- **Click target:** the entire header cell (not just the icon)
- **Cycle on repeated click:** ascending → descending → unsorted → ascending

### Inline Filtering

History table filtering follows the instant-filter pattern:

- **Search input** above the table filters visible rows on every keystroke, debounced 150ms
- `Escape` clears input and restores all rows
- Active filter count: `"3 of 47 runs"` shown in dim text at top-right of the table
- **Strategy filter chips** below the search bar: clicking a strategy filters to that strategy; the chip shows `strategy-color · chain-of-thought [×]`; multiple chips combine as AND
- **Score range slider:** `7–10` range, shows a neon-cyan filled range track
- `Ctrl+F` (or `/` Vim-style) focuses the search input from anywhere within the history view

### Multi-Select and Bulk Actions

The standard for Project Synthesis's history tables:

- **Checkbox column** (32px wide, leftmost): appears on row hover; always visible when checked
- **Shift+click** selects a contiguous range from the last click to the current row
- **Ctrl+click** toggles individual row selection without affecting others
- **Clicking the row body** (not the checkbox) opens the run — it does not change selection
- **Bulk action bar:** appears fixed at the top of the table when any rows are selected; replaces the search/filter row:

```
[5 runs selected]  [Compare (2)]  [Export]  [Delete]   ×
```

`Compare` is only active when exactly 2 rows are selected. `Escape` deselects all and restores the search bar.

### Keyboard Navigation

```
↑ / ↓       Navigate rows (moves focus ring)
Enter        Open focused run (same as clicking row body)
Space        Toggle selection on focused row
Delete       Delete selected rows (shows confirmation for multi)
/            Focus search input (vim-style, matches GitHub/GitLab)
Escape       Clear selection, OR clear search if selection is empty
```

### Conditional Cell Formatting (Score Tinting)

Score cells in history tables use a **background tint** proportional to the score — not a full color, but a hint:

- Score 9–10: `bg-neon-green/8` cell background
- Score 7–8: `bg-neon-cyan/8` cell background
- Score 4–6: `bg-neon-yellow/6` cell background
- Score 1–3: `bg-neon-red/6` cell background

At 6–8% opacity, the tint is perceptible in a column scan but does not compete with the score value text. This is the W&B pattern for metric cell coloring.

### Inline Score Delta

When a prompt has been forged more than once, the History sub-tab shows score deltas vs. the immediately prior run:

```
Run 4   chain-of-thought   8.1   +0.4   14.2s
Run 3   co-star            7.7    —     18.9s
Run 2   co-star            7.7   +0.6   22.1s
Run 1   chain-of-thought   7.1    —     31.4s
```

- Positive delta: `neon-green` Geist Mono (e.g., `+0.4`)
- Negative delta: `neon-red` (e.g., `-0.3`)
- Zero delta: `text-dim` (e.g., `—`)
- First run: `text-dim` `—` (no prior to compare against)

### "Pin to Top" for Best Runs

MLflow pattern: a **pin icon** appears on row hover in the History sub-tab. Pinned runs always appear at the top of the list regardless of sort order, with a subtle `border-l-2 neon-cyan/30` left accent. This enables the workflow: pin the best-known run and iterate against it visually.

Maximum 3 pinned runs. Attempting to pin a 4th shows a toast: `"Max 3 runs pinned — unpin one first"`.

### Column Auto-Sizing

Double-clicking a column's resize handle auto-sizes that column to fit the widest visible cell content (TablePlus/DataGrip pattern). Implementation: measure the max content width of all visible rows in that column, set `--col-N-width` to that value + 8px padding.

---

## 22. Skeleton Loading System

### Shimmer Specification

Every loading state in Project Synthesis uses the standard shimmer skeleton — not a spinner, not a "Loading..." text.

```css
@keyframes shimmer {
  0%   { background-position: -200% center; }
  100% { background-position:  200% center; }
}

.skeleton {
  background: linear-gradient(
    90deg,
    var(--color-bg-card) 25%,
    color-mix(in srgb, var(--color-bg-card) 70%, var(--color-bg-hover) 30%) 50%,
    var(--color-bg-card) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
  border-radius: var(--radius-sm, 4px);
}
```

**No glow in the shimmer gradient.** The highlight band uses `color-mix` to shift slightly toward `bg-hover` — not a white highlight, not a glow effect.

**Animation duration:** 1.5s per cycle. Do not deviate — slower feels broken, faster feels anxious.

### Shape Accuracy Requirements

Skeleton placeholder shapes **must exactly match** the real content's column widths and element heights. If the score badge column is 48px wide with a 20px circle, the skeleton placeholder must be 48px × 20px. Generic full-width gray bars are forbidden.

**Navigator file tree row (40px height):**
- Icon placeholder: 14px × 14px, `border-radius: 50%`
- Name placeholder: fills remaining width minus icon and right badge
- Badge placeholder: 28px × 16px (for score badge position)

**History table row (32px height):**
- Checkbox: 16px × 16px
- Strategy chip: 80px × 18px, `border-radius: 9999px`
- Score circle: 20px × 20px, `border-radius: 50%`
- Title: fills 1fr
- Time: 48px × 12px

**Inspector section:**
- Section heading: 60px × 10px
- Data row: full-width × 12px

### Row Stagger Delay

When loading a list (Navigator, History), skeleton rows stagger with a cascade delay:

```css
.skeleton-row:nth-child(n) {
  animation-delay: calc(var(--row-index, 0) * 40ms);
}
```

Render 8–12 skeleton rows regardless of expected data count (the user's viewport determines what's visible). Do not pre-size the skeleton list to the exact data count — that leaks implementation details.

### Minimum Display Duration

Skeleton states show for a **minimum of 200ms** even if data arrives faster. This prevents a jarring flash-of-skeleton on fast connections.

```typescript
const MIN_SKELETON_MS = 200;
const skeletonStart = Date.now();
const data = await fetchHistory();
const elapsed = Date.now() - skeletonStart;
if (elapsed < MIN_SKELETON_MS) {
  await delay(MIN_SKELETON_MS - elapsed);
}
setData(data);
```

If loading exceeds 800ms, display a subtle `"Loading..."` label in the Status Bar (dim, 10px Geist Mono) in addition to the skeleton — to reassure the user the system is not stuck.

---

## 23. Streaming Output Display

### Token Rendering — requestAnimationFrame Batching

The core streaming performance requirement: **never update the DOM on every individual SSE token event**. LLM streams can deliver 50–100 tokens per second. At that rate, direct DOM updates cause 60+ re-renders per second, text flickering, and layout thrashing.

The correct pattern: accumulate tokens in a buffer and flush to DOM once per animation frame:

```typescript
class StreamingRenderer {
  private buffer = '';
  private frameScheduled = false;
  private container: HTMLElement;

  constructor(container: HTMLElement) {
    this.container = container;
  }

  onToken(token: string): void {
    this.buffer += token;
    if (!this.frameScheduled) {
      this.frameScheduled = true;
      requestAnimationFrame(() => {
        // Append buffered content in a single DOM write
        const text = document.createTextNode(this.buffer);
        this.container.appendChild(text);
        this.buffer = '';
        this.frameScheduled = false;
      });
    }
  }

  onComplete(): void {
    // Flush any remaining buffer immediately
    if (this.buffer) {
      this.container.appendChild(document.createTextNode(this.buffer));
      this.buffer = '';
    }
    this.hideCursor();
  }
}
```

This caps DOM updates at 60fps regardless of SSE token rate. The streaming text appears smooth, with no flickering or horizontal jitter.

### Streaming Cursor

The streaming cursor signals that the stream is live. It is a **DOM element at the end of the streaming content**, not the CSS cursor:

```html
<span
  class="streaming-cursor"
  aria-hidden="true"
  style="display: inline-block; width: 2px; height: 21px; vertical-align: text-bottom;"
></span>
```

```css
.streaming-cursor {
  background-color: var(--color-neon-cyan);
  animation: cursor-blink 1000ms step-end infinite;
}

@keyframes cursor-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0; }
}
```

**Height:** exactly the line-height of the streaming text container (`21px` for `text-sm leading-[1.5]` at 14px). Not `h-3` (12px) — that creates a visible height mismatch.

**On stream completion:** `cursor.remove()` — don't set `display: none`, remove the element entirely to avoid any layout artifacts.

### Streaming Monospace vs. Proportional Rule

| Content Type | Font | Rationale |
|---|---|---|
| Streaming optimized prompt text | `font-mono` (Geist Mono 13px) | Monospace prevents horizontal reflow jitter as characters arrive |
| Streaming analysis text (Stage 01) | `font-sans` (Geist 13px) | Readable prose; reflow acceptable at this stage's line-by-line rate |
| Streaming strategy reasoning (Stage 02) | `font-sans` (Geist 13px) | Narrative text |
| Tool call feed (Stage 00 Explore) | `font-mono` (Geist Mono 11px) | Terminal-style; needs alignment for file paths and tool names |

### Markdown Rendering Rule

**Never render markdown during streaming.** Stream all content as plain text. Apply markdown rendering (bold, headers, code blocks, lists) only after the stream event `complete` fires.

**Why:** Rendering markdown while tokens arrive causes mid-word DOM node creation/destruction. When `**` arrives, the renderer doesn't know if it's opening bold or an escaped asterisk until more tokens arrive. This causes visible flickering and layout instability.

**After `complete`:** Replace the `textContent` of the streaming container with a rendered markdown version (using the existing markdown renderer). The transition is instant and invisible to users.

### Scroll Buffering for Long Outputs

For Stage 00 (Explore) tool call feeds that may produce 100+ lines:

- Cap the visible streaming area at **50 visible lines** in the expanded stage card
- When more than 50 lines exist, show a dim header: `"... 47 earlier entries"` above the visible window
- The scrollback uses a **ring buffer** in memory: new lines replace oldest lines when buffer exceeds 200 entries
- After stream completion, the "View full output" link in the stage header opens the full trace in the Inspector panel (no inline scroll)

This prevents the Stage Track from growing to dominate the viewport during long Explore stages.

---

## 24. Research-Driven Patterns to Implement

Concrete patterns sourced from industry tools that should be implemented in Project Synthesis, organized by component.

### From W&B (Run-Color Anchoring)

When comparing two forge runs in the `DiffView` or the History sub-tab's compare mode: assign each run a distinct accent color (`neon-cyan` for run A, `neon-purple` for run B) and use that color consistently across:
- The diff view column headers
- Score bar fill colors
- Score delta labels
- Strategy badge borders

This makes it impossible to lose track of which side is which, even in a complex side-by-side view.

### From Langfuse (TTFT Split Bar)

The stage duration display in the Stage Track header currently shows a single `14.2s`. Split it into two segments for `optimize` and `validate` stages where TTFT is meaningful:

```
03 // OPTIMIZE    OPUS    ✓   [████░░░░] 2.1s wait + 16.1s stream   892 tok
```

The split bar uses: `neon-cyan/20` for the "waiting" segment and `neon-cyan/60` for the "streaming" segment. Total = `wait_ms + stream_ms`. This is immediately informative for diagnosing whether slowness is model latency vs. response length.

### From Braintrust (Score-as-Span)

When expanding Stage 04 (Validate) in the Stage Track, render the 5 dimension scores as **inline child rows beneath the stage header**, not just a summary line:

```
● ─── 04 // VALIDATE     SONNET    ✓  4.1s   368 tok
      └── clarity        [████████░░]  8/10
      └── specificity    [█████████░]  9/10
      └── structure      [███████░░░]  7/10
      └── faithfulness   [████████░░]  8/10
      └── conciseness    [████████░░]  8/10
           overall       [████████░░]  8.1/10  ●
```

Each dimension row uses a proportional fill bar (neon-cyan colored, 5px height) and the score integer (Geist Mono 10px). This eliminates the need to navigate to the Inspector to read scores — they're visible in the pipeline trace.

### From Neptune.ai (Show Differences Only)

In the compare view (`DiffView`, sub-tab `[Diff]`): add a toggle `[Show differences only]` that hides structural sections where the original and optimized prompts are identical, revealing only the paragraphs/sentences that changed.

This is implemented as a **computed split** of the two texts into matching/changed segments (LCS-based diff), then filtering the view to show only `changed` segments plus 2 lines of context before/after each change. The toggle is `btn-ghost` at the top-right of the DiffView panel.

### From GitHub Actions (Inline Failure Annotation)

When a stage fails, show the error summary **inline in the stage header row** — not only in the expanded body:

```
● ─── 03 // OPTIMIZE    OPUS    ✗   8.7s   JSON parse error: unexpected token at pos…
```

The error text (`neon-red/70`, Geist Mono 10px) replaces what would be the duration/token badges. Clicking the stage header expands the full error with the trace and a "Retry" action.

### From MLflow (Pin Best Run)

Per the History sub-tab section: allow pinning up to 3 runs to the top of the list. This is the primary mechanism for "anchored comparison" — keep the current best visible while iterating new runs against it.

### Column Auto-Sizing (Double-Click Resize Handle)

In the History sub-tab table and Navigator History list: double-clicking the resize handle between column headers auto-sizes the column to its maximum content width (DataGrip/TablePlus pattern). Reduces manual fiddling with column widths when prompt titles vary significantly in length.
