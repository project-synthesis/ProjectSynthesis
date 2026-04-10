# Component Patterns Reference

## Table of Contents
- [Button Styles](#button-styles)
- [Card Patterns](#card-patterns)
- [Chips & Badges](#chips--badges)
- [Input Fields](#input-fields)
- [Score Circles](#score-circles)
- [Strategy Bar](#strategy-bar-premium-glass)
- [Pipeline Timeline](#pipeline-timeline)
- [Sidebar Tabs](#sidebar-tabs-pill-segment-control)
- [Hover State Recipes](#hover-state-recipes)
- [Keyframe Animations](#keyframe-animations)
- [Transition Timing](#transition-timing)

---

## Button Styles

| Variant | Class | Background | Text | Border |
|---------|-------|------------|------|--------|
| Primary | `.btn-primary` | Transparent → `rgba(168, 85, 247, 0.1)` on hover | neon-purple | `1px solid neon-purple` |
| Outline Primary | `.btn-outline-primary` | `rgba(0, 229, 255, 0.05)` | neon-cyan | `rgba(0, 229, 255, 0.2)` |
| Outline Secondary | `.btn-outline-secondary` | Transparent | text-secondary | border-subtle |
| Outline Danger | `.btn-outline-danger` | `rgba(255, 51, 102, 0.05)` | neon-red | `rgba(255, 51, 102, 0.2)` |
| Ghost | `.btn-ghost` | Transparent | Inherited | None |
| Icon | `.btn-icon` | Transparent | text-dim → text-primary on hover | None |
| Icon Danger | `.btn-icon-danger` | Transparent | neon-red (50% → 100% on hover) | `rgba(255, 51, 102, 0.1)` |

**Primary button interaction:**
- Hover: `translateY(-1px)` + sharp neon border
- Active: `translateY(0)` + muted neon border
- Disabled: `opacity: 0.4`, `cursor: not-allowed`

---

## Card Patterns

| Pattern | Class | Border Radius | Effect |
|---------|-------|---------------|--------|
| Card hover outline | `.card-outline` | Inherited | Cyan border contour on hover |
| Card top outline | `.card-top-outline`| Inherited | Cyan gradient line at top on hover |
| Prompt card | `.prompt-card` | 12px | Background shift + sharp cyan border on hover |
| Project header | `.project-header-card` | 16px | Cyan-to-purple gradient line at top on hover |
| Sidebar card | `.sidebar-card` | Inherited | 2px left accent border on hover/focus |

---

## Chips & Badges

| Variant | Class | Border Radius | Font | Size |
|---------|-------|---------------|------|------|
| Chip (pill) | `.chip` | 9999px | Geist Mono | 10px |
| Chip (rect) | `.chip.chip-rect` | 6px | Geist Mono | 10px |
| Badge | `.badge` | 6px | Geist Mono | 10px |
| Badge (small) | `.badge-sm` | 9999px | Geist Mono | 9px |
| Tag chip | `.tag-chip` | 9999px | Geist Mono | 10px |

**Tag chip colors:** neon-green at 60% opacity, with green-tinted background and border.

---

## Input Fields

| Pattern | Class | Focus Effect |
|---------|-------|-------------|
| Standard input | `.input-field` | Cyan border + sharp cyan contour |
| Select field | `.select-field` | Cyan border + sharp cyan contour |
| Context input | `.ctx-input` | Green border + sharp green contour |

---

## Score Circles

```css
.score-circle {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  box-shadow: inset 0 0 0 1.5px currentColor;
  background: color-mix(in srgb, currentColor 8%, transparent);
}
```

Small variant (`.score-circle-sm`): 20px, 10px font (same as standard in compact layout).

---

## Strategy Bar (Premium Glass)

```css
.strategy-bar-primary {
  box-shadow:
    inset 0 1px 0 0 rgba(255, 255, 255, 0.14),
    inset 0 -1px 0 0 rgba(0, 0, 0, 0.2),
    inset 0 0 0 1px var(--bar-accent);
  /* ::after pseudo-element adds top-half highlight gradient */
}
```

---

## Pipeline Timeline

- **Node:** 12px circle, `z-index: 1`
- **Connector:** 2px wide vertical line with gradient from `--pipeline-color-from` to `--pipeline-color-to`
- **Finding highlight:** 6px border-radius, tinted background with 5% opacity of highlight color, 1px inset contour ring at 20%

---

## Sidebar Tabs (Pill Segment Control)

- Container: 8px radius, `color-mix(var(--color-bg-hover) 40%, transparent)`, 2px padding
- Tab: 6px radius, 11px font, 600 weight
- Active tab: `color-mix(var(--color-neon-cyan) 8%, transparent)` with sharp cyan border

---

## Hover State Recipes

Multi-property hover choreography patterns. All properties animate together — never stagger individual channels.

### Recipe A: Card Hover (Border + Background)

Used by sidebar cards (HistoryEntry, ProjectItem).

```
/* Resting */
border: 1px solid var(--color-border-subtle);
background: transparent;

/* Hover — 2 simultaneous changes */
hover:border-border-accent            /* border shifts to sharp cyan */
hover:bg-bg-hover/40                  /* background lightens */
transition: 200ms                     /* all properties together */
```

### Recipe B: Accent Button Hover (Border Contour + Background)

Used by header toggles, action buttons, outline buttons.

```
/* Resting */
bg-neon-cyan/8

/* Hover — opacity increase + sharp border appears */
hover:bg-neon-cyan/15
hover:border-neon-cyan
transition: 200ms
```

### Recipe C: Chip/Filter Hover (Border + Text Brightening)

Used by template chips, filter chips, context fields.

```
/* Resting */
border-border-subtle text-text-dim

/* Hover — border takes accent color, text brightens */
hover:border-neon-green/25
hover:text-neon-green/80
transition: 200ms
```

### Recipe D: Full Contrast Hover (Border + Background + Text)

Used by recommendation buttons, insight actions — the most elaborate hover.

```
/* Resting */
border-neon-yellow/20 bg-neon-yellow/[0.06] text-neon-yellow/80

/* Hover — all 3 channels intensify to flat neon */
hover:border-neon-yellow
hover:bg-neon-yellow/[0.12]
hover:text-neon-yellow
transition: 200ms
```

### Recipe E: Lift Hover (Transform + Border)

Used only by `.btn-primary`. The most dramatic interaction.

```
/* Hover — physical lift + sharp neon border */
hover:translateY(-1px)
hover:border-neon-cyan

/* Active — settle back */
active:translateY(0)
active:border-neon-cyan/50
transition: 250ms cubic-bezier(0.16, 1, 0.3, 1)
```

---

## Keyframe Animations

| Name | Duration | Easing | Effect | Use Case |
|------|----------|--------|--------|----------|
| `fade-in` | 400ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Translate Y(10px) + fade | General entrance |
| `stagger-fade-in` | 350ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Translate Y(8px) + fade | List item stagger |
| `slide-in-right` | 300ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Translate X(20px) + fade | Toast entrance |
| `slide-out-right` | 300ms | `cubic-bezier(0.4, 0, 1, 1)` | Translate X(20px) + fade out | Toast exit |
| `slide-up-in` | 200ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Translate Y(6px) + fade | Subtle upward entrance |
| `scale-in` | 300ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Scale(0.95) + fade | Modal/panel entrance |
| `dialog-in` | 300ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Scale(0.95) + fade (centered) | Dialog entrance |
| `dropdown-enter` | 200ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Scale(0.96) + Y(4px) + fade | Dropdown open (downward) |
| `dropdown-enter-up` | 200ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Scale(0.96) + Y(-4px) + fade | Dropdown open (upward) |
| `dropdown-exit` | 150ms | `cubic-bezier(0.4, 0, 1, 1)` | Scale(0.96) + Y(4px) + fade out | Dropdown close (downward) |
| `dropdown-exit-up` | 150ms | `cubic-bezier(0.4, 0, 1, 1)` | Scale(0.96) + Y(-4px) + fade out | Dropdown close (upward) |
| `section-expand` | 300ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Max-height 0→500px + fade | Collapsible section |
| `copy-flash` | 600ms | `ease-out` | Green flash (`#22ff88`) | Copy-to-clipboard feedback |
| `shimmer` | 1500ms | `ease-in-out` (infinite) | Horizontal gradient sweep | Skeleton loading |
| `gradient-flow` | varies | linear (infinite) | Background position cycle | Animated gradient backgrounds |
| `status-pulse` | 3s | `ease-in-out` (3 iterations) | Green background pulse | Status dot indicators |
| `forge-spark` | varies | ease (infinite) | Yellow flash + scale(1.2) + rotation | Forge action sparks |

---

## Transition Timing

| Duration | Easing | Use Case |
|----------|--------|----------|
| 150ms | `ease` | Micro-interactions: icon color, text color, tiny state flips |
| 200ms | `ease` / `cubic-bezier(0.16, 1, 0.3, 1)` | Standard hover: border-color, background, text-color, button states |
| 300ms | `ease` | Structural changes: focus rings, border-color + background on inputs, card reveals |
| 500ms | `ease` | Progress bar fills, complex container transitions |

**Multi-property transitions** always animate together in a single declaration:

```css
transition: border-color 0.3s ease, background-color 0.3s ease;           /* card-hover */
transition: background-color 0.15s, color 0.15s, border-color 0.15s; /* buttons */
transition: all 0.2s ease;                                           /* prompt-card */
transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);                 /* iteration timeline */
```

---

## Disclosure Transitions

All expand/collapse panels use Svelte's `transition:slide` directive — NOT CSS keyframe animations. This ensures proper frame-scheduled enter/exit with no jitter.

```svelte
<!-- Standard disclosure pattern -->
{#if expanded}
  <div transition:slide={{ duration: 200 }}>
    <!-- Content -->
  </div>
{/if}
```

| Context | Duration | Notes |
|---------|----------|-------|
| Section expand (L1, tech details, filters) | 200ms | Primary disclosure speed |
| Sub-section expand (L2 journey, framework) | 150ms | Faster for nested content |
| Panel entrance (FeedbackTier2, RefinementInput) | 200ms | Same as section expand |

**Never use** CSS `@keyframes` with `max-height` for disclosure — it triggers layout reflow and is not GPU-composited. The `transition:slide` directive handles height animation internally with proper Svelte lifecycle hooks.

**Never use** `requestAnimationFrame` + `visible` state hacks for entrance animations — Svelte's transition system handles this correctly.
