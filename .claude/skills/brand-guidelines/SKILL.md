---
name: brand-guidelines
description: >
  Project Synthesis brand guidelines, design system, aesthetics, theme, and visual identity reference.
  Use this skill whenever the user mentions brand, branding, brand guidelines, aesthetics, visual
  design, design system, theme, color scheme, color system, tier colors, tier aesthetics, styling,
  look and feel, UI design, or any variation of these terms. Also use when implementing, reviewing,
  or modifying frontend components, UI elements, CSS, Tailwind classes, colors, typography, animations,
  spacing, hover states, component patterns, or animation timing. This is the single source of truth
  for the industrial cyberpunk aesthetic — dark backgrounds, sharp 1px neon contours, chromatic data
  encoding, tier-specific accent colors (internal=cyan, sampling=green, passthrough=yellow), domain
  color coding, and ultra-compact density. Covers the zero-effects directive, neon palette, background
  hierarchy, motion system, interactive states, voice and tone, and anti-patterns. If you're touching
  anything visual in Project Synthesis or the user asks about how things should look, this skill applies.
---

# Project Synthesis Brand Guidelines

## Brand Essence

Project Synthesis is an **AI-powered prompt optimization tool** — a precision instrument that takes raw prompts and returns quantifiably better versions through a 3-phase pipeline: analyze, optimize, score. The metaphor is industrial synthesis: raw input goes in, multi-phase transformation happens, and something measurably improved comes out.

The tagline — **"AI-Powered Prompt Optimization"** — is deliberately technical and direct. No marketing fluff. It tells you exactly what it does.

**In summary**: Project Synthesis's brand identity is *industrial cyberpunk precision tooling*. It's a synthesis engine, not a playground. The visual language communicates through sharp neon emission on dark fields, chromatic data encoding, monospace numerics, and spring-loaded motion. The voice is that of a confident, data-driven instrument — never casual, never vague. Every design decision traces back to the core metaphor: raw prompts go in, 3-phase pipeline transformation happens, and something quantifiably better comes out.

---

## Design Principles

Five axioms that govern every visual decision. When two choices seem equally valid, these break the tie — in order of precedence.

### 1. Signal Over Noise

Every pixel must carry information or actively support information that does. Decorative elements are permitted only when they encode data (chromatic strategy colors, score-circle hues, complexity-tier badges). If a visual element can be removed without reducing the user's ability to read, compare, or act on data — remove it. **Metric:** Data-ink ratio. Maximize relentlessly.

### 2. The Neon Tube Model

All visual accents behave like precisely bent neon tube signage:

| Property | Rule | Rationale |
|----------|------|-----------|
| Width | Uniform 1px. Never thicker on one side, never tapered. | Real neon tubes have consistent diameter. |
| Brightness | Pure saturation at the defined hex value. Never faded, never oversaturated. | A lit tube is either on or off. |
| Falloff | Zero. The border is the accent. Nothing bleeds, blurs, or radiates beyond it. | Light stops at the glass surface. |
| Corners | Sharp bends only. Borders follow the element's `border-radius`. | Neon tubes are bent at precise angles. |
| Color | One color per tube. No per-segment gradients within a single border ring. | Each tube = one gas mixture = one emission color. |

### 3. Darkness as Active Design

The dark background is the forge's unlit chamber. It performs three functions: **contrast amplifier** (neon contours are maximally visible against near-black), **spatial organizer** (the 5-tier background hierarchy creates depth without shadows), and **rest state** (absence of color is the default; interaction brings elements to life).

### 4. Chromatic Encoding

Color is data, not decoration. Every neon color has a fixed semantic assignment (strategy, task type, complexity, system state). Never use a neon color for purely aesthetic purposes.

### 5. Mechanical Responsiveness

Interactions feel like precision hardware — immediate, tactile, and deterministic. Spring entrance curve, accelerating exit curve. Multi-property transitions fire simultaneously as one atomic event.

---

## Design Philosophy

### Zero-Effects Directive

**STRICT DIRECTIVE: There are ZERO glow effects, drop shadows, text-shadow blooms, diffuse box-shadows, or soft radiance of any kind anywhere in the UI. Absolute and non-negotiable.**

| Property | Banned Usage | Permitted Usage |
|----------|-------------|-----------------|
| `box-shadow` | Any value with spread or blur | `inset 0 0 0 Npx` only (contour ring) |
| `text-shadow` | All usage | None. Never. |
| `filter: drop-shadow()` | All usage | None. |
| `radial-gradient` | Fading to transparent around elements | Data visualizations only |
| `@keyframes` | Pulse, breathe, radiate outward | Scale, translate, opacity-in/out, rotation |

The word **"glow"** is banned from code comments, class names, and specs. Replacements: **contour** (sharp 1px border), **tint** (color fill), **flash** (brief feedback), **emission** (visual presence of lit element).

### Dark-First Flat-Neon Contours

"Sharper contour emission = more interactive." Every interactive state is a *solid geometric neon border or pure color fill*. Depth via background opacity shifts and overlapping sharp borders, never ambient occlusion.

### Contour Intensity Grammar

| Tier | Visual Cue | Use Case |
|------|-------------|----------|
| Micro | 1px border color shift | Status dots, switch thumbs, sidebar tab active |
| Small | 1px color border + faint inset background | Button hover, header toggles |
| Medium | 1px solid neon + opaque background (`/12`) | Focus states, recommendation buttons |
| Large | Sharp `inset 0 0 0 1px` inner contour | Card hover, prompt card focus |
| Hero | Pure solid vector highlighting + lift | Primary button resting/hover |

### Inset Contours (Active States)

```css
/* Active timeline item */
box-shadow: inset 0 0 0 1px rgba(0, 229, 255, 0.4);

/* Strategy bar premium glass — 3-layer rigid inset */
box-shadow:
  inset 0 1px 0 0 rgba(255, 255, 255, 0.14),
  inset 0 -1px 0 0 rgba(0, 0, 0, 0.2),
  inset 0 0 0 1px var(--bar-accent);
```

### Elevation Without Shadows

```css
/* Card elevation — background shift + contour */
border: 1px solid var(--color-border-subtle);
background: color-mix(in srgb, var(--color-bg-card) 98%, white 2%);

/* Primary button — physical elevation */
transform: translateY(-1px);
border: 1px solid var(--color-neon-cyan);
```

### Ultra-Compact Density

Denser than VS Code — near-zero padding, Excel-like data hierarchy. Maximize information per pixel. **When in doubt, use less padding, not more.**

**The golden rule:** Every element height and padding should be the minimum that keeps text legible and click targets usable. Default to `h-5` (20px) for interactive elements, `p-1` (4px) for containers. Only increase when readability demands it.

**IDE-wide layout standard:**

| Element | Height | Padding | Text | Notes |
|---------|--------|---------|------|-------|
| Document tab bar | 28px | `px-2 gap-1` | `text-[11px]` | Tabs themselves: `px-1.5 gap-0.5` |
| Sidebar section headers | 24px | `px-1.5` | `text-[11px] font-bold uppercase` | Minimal chrome — the heading IS the divider |
| Toolbar bars (strategy, actions) | 28px | `px-1.5 gap-1.5` | `text-[11px]` | Tight row of controls |
| Sidebar content | — | `p-1.5` (6px) | — | Sections separated by `space-y-1.5` (6px) |
| Content areas | — | `p-1.5` (6px) | — | Card interiors `p-1` (4px) |
| Data rows | 20px | `px-1` | `text-[10px]` | `flex items-center`, labels in dim |
| Buttons (inline/action) | 20px | `px-2` | `text-[10px]` | `line-height: 18px`. Never taller than data rows. |
| Select/input fields | 20px | `px-1` | `text-[11px]` | Same height as buttons — flush alignment |
| Status bar | 22px | `px-1.5` | `text-[10px] font-mono` | Thinnest fixed element |
| Activity bar icons | 32px × 32px | — | — | Icon: 16px. Centered. |

**Banned values inside sidebars and toolbars:**
- `p-2` (8px) or larger — use `p-1.5` (6px) maximum
- `p-3` (12px) or larger — absolutely never
- `h-8` (32px) for headers — use 24px
- `h-7` (28px) for buttons — use 20px
- `gap-2` (8px) — use `gap-1.5` (6px) maximum
- `px-2.5` (10px) or larger on tabs/buttons — use `px-2` (8px) maximum

**The test:** If you can remove 2px of padding and nothing overlaps or becomes unreadable, you have too much padding. Strip it.

### Glass Morphism

Semi-transparent surfaces (`color-mix` at 50–98% opacity), backdrop blur for physical presence. Glass panels never cast a shadow.

```css
.glass { background: color-mix(in srgb, var(--color-bg-secondary) 92%, transparent); }
/* Collapsible: 50%, Accent-tinted: 98%, Blur: 4px light / 8px medium */
```

---

## Color System

**10 neon signal colors on a 6-tier dark background.** Chromatic encoding — color *is* data.

### Primary Brand Accent
- **Neon Cyan:** `#00e5ff` — Primary actions, focus states, brand identity

### Neon Palette

| Token | Hex | CSS Variable | Semantic |
|-------|-----|-------------|----------|
| neon-cyan | `#00e5ff` | `--color-neon-cyan` | Primary identity, primary actions |
| neon-purple | `#a855f7` | `--color-neon-purple` | Processed, elevated |
| neon-green | `#22ff88` | `--color-neon-green` | Health, context, success |
| neon-red | `#ff3366` | `--color-neon-red` | Danger, destruction |
| neon-yellow | `#fbbf24` | `--color-neon-yellow` | Alchemical fire, warnings |
| neon-orange | `#ff8c00` | `--color-neon-orange` | Attention, alerts |
| neon-blue | `#4d8eff` | `--color-neon-blue` | Information, analysis |
| neon-pink | `#ff6eb4` | `--color-neon-pink` | Creativity |
| neon-teal | `#00d4aa` | `--color-neon-teal` | Secondary success, extraction |
| neon-indigo | `#7b61ff` | `--color-neon-indigo` | Reasoning |

### Background Hierarchy

| Token | Hex | CSS Variable | Purpose |
|-------|-----|-------------|---------|
| bg-primary | `#06060c` | `--color-bg-primary` | Page background (deepest dark) |
| bg-input | `#0a0a14` | `--color-bg-input` | Input fields, recessed wells |
| bg-secondary | `#0c0c16` | `--color-bg-secondary` | Glass panels, secondary surfaces |
| bg-card | `#11111e` | `--color-bg-card` | Cards, elevated panels |
| bg-hover | `#16162a` | `--color-bg-hover` | Hover states, active surfaces |
| bg-glass | `rgba(12, 12, 22, 0.7)` | `--color-bg-glass` | Glass morphism overlay |

### Text Hierarchy

| Token | Hex | CSS Variable | Purpose |
|-------|-----|-------------|---------|
| text-primary | `#e4e4f0` | `--color-text-primary` | Headlines, body text, values |
| text-secondary | `#8b8ba8` | `--color-text-secondary` | Descriptions, secondary labels |
| text-dim | `#7a7a9e` | `--color-text-dim` | Timestamps, metadata, disabled |

Never use pure white (`#fff`) for body text. `text-primary` has a blue cast that integrates with the palette. Pure white only for `::selection`.

### Borders

| Token | Value | CSS Variable |
|-------|-------|-------------|
| border-subtle | `rgba(74, 74, 106, 0.15)` | `--color-border-subtle` |
| border-accent | `rgba(0, 229, 255, 0.12)` | `--color-border-accent` |

### Brand Gradient

`linear-gradient(135deg, #00e5ff 0%, #7c3aed 50%, #a855f7 100%)` — the transformation arc from raw (cyan) to optimized (purple). Always flows cyan → purple, never reversed. Max 2 gradient elements per viewport.

---

## Typography

| Purpose | Font | Fallback | CSS Variable |
|---------|------|----------|-------------|
| UI Text / Body | Space Grotesk | Geist, Inter, ui-sans-serif, system-ui, sans-serif | `--font-sans` |
| Code / Data | Geist Mono | JetBrains Mono, ui-monospace, monospace | `--font-mono` |
| Display / Headings | Syne | Space Grotesk, Geist, ui-sans-serif, system-ui, sans-serif | `--font-display` |

**Space Grotesk** is the primary UI font — a geometric sans-serif with angular terminals and open apertures that reinforces the industrial cyberpunk aesthetic. Loaded via Google Fonts (`300;400;500;600;700`). Geist remains as the first fallback.

### Type Scale

| Class | Size | Weight | Font | Use |
|-------|------|--------|------|-----|
| Section heading | 11px | 700 | Syne | Uppercase, `letter-spacing: 0.1em` |
| Sub-section heading | 10px (`text-[10px]`) | 700 | Syne | Uppercase, `letter-spacing: 0.1em` — accordion titles, modal sub-headings, card section labels |
| Body | 12px (`text-xs`) | 400 | Space Grotesk | Standard UI text, prompt content |
| Compact label | 10px (`text-[10px]`) | 400 | Space Grotesk | Metadata labels, sidebar rows |
| Badge | 10px | 500 | Geist Mono | Badges, chips, metadata values |
| Badge (small) | 9px | 500 | Geist Mono | Compact badges, sub-labels |
| Micro label | 8-9px | 500 | Geist Mono | Elasticity labels, shift percentages |
| Input field | 11px | 400 | Space Grotesk | Form inputs, textareas |
| Select field | 11px | 500 | Space Grotesk | Dropdown selects |
| Score circle | 10px | 700 | Geist Mono | Score display (20px circle) |

### Rules

- **Section headings:** `font-display` (Syne), uppercase, `letter-spacing: 0.1em`, weight 700, `text-[11px]`
- **Monospace data:** `font-mono` for scores, badges, chips, tags, metadata, strategy names, numerics
- **Body text:** `font-sans` (Space Grotesk) at `text-xs` (12px) — NOT 14px
- **Compact labels:** `text-[10px]` for sidebar metadata, dimension labels, secondary info
- **Gradient text:** `.text-gradient-forge` (cyan → purple, `background-clip: text`)
- **Tabular figures:** All score displays use Geist Mono — digits occupy equal width for column alignment
- **Number + unit:** No space between number and unit: `8.2/10`, `42%`, `3.1pts`
- **Inline metrics:** Wrap in `<span class="font-mono">` to create visual "data callout"

---

## Animations

Spring entrance (`cubic-bezier(0.16, 1, 0.3, 1)`), accelerating exit (`cubic-bezier(0.4, 0, 1, 1)`). All motion respects `prefers-reduced-motion` → `0.01ms` duration.

### Forge Motion Personality

| Stage | Metaphor | Motion | Example |
|-------|----------|--------|---------|
| Analyze | Heating the metal | Rising upward | `fade-in`, `slide-up-in` |
| Strategy | Selecting the tool | Decisive lateral snap | `slide-in-right`, dropdowns |
| Optimize | Shaping under pressure | Compression then expansion | `scale-in`, `dialog-in` |
| Validate | Testing the temper | Brief sharp flash | `copy-flash`, score reveal |

Signature: **forge-spark** — yellow flash + scale(1.2) + rotation on the forge action button.

### Conventions

- **Primary easing:** `cubic-bezier(0.16, 1, 0.3, 1)` — all entrances
- **Exit easing:** `cubic-bezier(0.4, 0, 1, 1)` — all exits. No exceptions.
- **Fill mode:** `forwards` one-shot, `both` staggered
- **Transitions:** 150ms micro, 200ms hover, 300ms structural, 500ms progress fills
- **Multi-property transitions** always in a single declaration, same duration

For the full keyframe animation table and transition timing details, see `references/component-patterns.md`.

---

## Interactive State Machine

Every interactive element follows this 5-state lifecycle:

| State | Visual Treatment | Trigger |
|-------|-----------------|---------|
| **Resting** | Subtle border (`border-subtle`), no accent color. Dark, receded, waiting. | No interaction |
| **Hover** | Contour intensifies one tier. Background tints. Text brightens. 200ms all-at-once. | `:hover` |
| **Active** | Contour at max. Transform snaps to resting (`translateY(0)`). Border mutes. | `:active` |
| **Focus** | Cyan outline (`1px solid rgba(0, 229, 255, 0.3)`, offset 2px). Additive — overlays any state. | `:focus-visible` |
| **Disabled** | `opacity: 0.4`, `cursor: not-allowed`. No contour, tint, or transitions. | `disabled` |

```
Resting ──hover──→ Hover ──press──→ Active ──release──→ Hover ──leave──→ Resting
Focus can overlay any state. Disabled bypasses all states.
```

Hover → Active is a *contraction* (settling under pressure), not intensification. Disabled elements snap instantly — no fade-out.

---

## Voice & Tone

Technical over emotional, precise over vague. Like a confident instrument panel.

| Principle | Do | Don't |
|-----------|-------|--------|
| Technical | "Scored 8.2/10 on clarity" | "Your prompt is pretty clear!" |
| Confident | "Optimized using Chain of Thought" | "We tried to improve your prompt..." |
| Concise | "Forge" (button label) | "Click to optimize your prompt" |
| Direct | "Score improved +2.1" | "We're happy to report some improvement!" |

### Canon Terminology

| Use | Avoid |
|-----|-------|
| Forge / Optimize | Submit / Process |
| Strategy | Method / Technique |
| Pipeline | Workflow / Steps |
| Score (1-10) | Rating / Grade |
| Contour / Border | Glow / Shadow |
| Flash | Glow / Pulse / Radiance |
| Tint | Glow / Halo |
| Emission | Radiance / Bloom |
| Delta | Difference / Change |

**UI copy:** Button labels = single verb/noun. Empty states = factual. Errors = what happened + what to do. Tooltips = one technical line. Notifications = subject + metric.

---

## Anti-Patterns

If any of these appear in code, it is a bug:

| Anti-Pattern | Correct Alternative |
|-------------|---------------------|
| `box-shadow: 0 4px 12px rgba(...)` | `border: 1px solid` + background shift |
| `text-shadow: 0 0 8px #00e5ff` | Brighter text color or increased `font-weight` |
| `filter: drop-shadow(...)` | `border` or `outline` |
| `animation: pulse 2s infinite` | `copy-flash` (one-shot) or static contour |
| `radial-gradient(circle, #00e5ff, transparent)` | Solid `background-color` at low opacity |
| Opacity *decreasing* on hover | Opacity always *increases* on hover |
| `border: 2px solid` | `border: 1px solid` always |
| `color: white` / `#ffffff` in body | `var(--color-text-primary)` (#e4e4f0) |
| `font-family: monospace` for body text | Mono only for scores, badges, chips, metadata |
| Staggered transition declarations | Single `transition:` declaration, same duration |
| `.glow-effect` / `--glow-color` / `/* add glow */` | "contour," "tint," or "flash" |
| `z-index: 999` or unlisted value | Use one of the 9 defined layers |

---

## Reference Files

Detailed lookup tables are split into reference files to keep this core document focused. Read the relevant reference when you need specific values:

| Reference | Contents | Read when... |
|-----------|----------|-------------|
| `references/color-mappings.md` | Strategy colors (10), task type colors (14), complexity colors (3), score-to-color mapping, data visualization conventions | Implementing strategy badges, task type chips, score displays, comparative views |
| `references/component-patterns.md` | Button styles, card patterns, chips/badges, inputs, score circles, strategy bar, pipeline timeline, sidebar tabs, hover state recipes (5 recipes), keyframe animations (17), transition timing | Building or modifying any UI component, adding hover states, choosing animation |
| `references/layout-and-accessibility.md` | Border radius (6 tiers), opacity tiers (bg/border/text), spacing system (padding/gap/rhythm), icon sizing, z-index layers, color-mix patterns, scrollbar, selection, accessibility (focus rings, reduced motion, sr-only, skip link, WCAG contrast) | Setting spacing, radius, opacity, z-index, or implementing accessibility features |

---

## Brand Identity

| Attribute | Value |
|-----------|-------|
| **Name** | Project Synthesis |
| **Tagline** | "AI-Powered Prompt Optimization" |
| **Type** | Open Source Web Application (Apache 2.0 License) |
| **Deployment** | Self-hosted (Docker or local dev) |
| **Domain** | zenresources.net |
| **Target user** | Developers, prompt engineers, technical practitioners |
| **Contacts** | brand@ / support@ / legal@ / security@ zenresources.net |
