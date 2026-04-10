# Layout & Accessibility Reference

## Table of Contents
- [Border Radius System](#border-radius-system)
- [Opacity Tiers](#opacity-tiers)
- [Spacing System](#spacing-system)
- [Icon Sizing](#icon-sizing)
- [Z-Index Layering](#z-index-layering)
- [Color-Mix Patterns](#color-mix-patterns)
- [Scrollbar](#scrollbar)
- [Selection](#selection)
- [Accessibility](#accessibility)

---

## Border Radius System

Flat edges are the default. Rounding is the exception, reserved for specific pill/chip shapes.

| Tier | Radius | Token | Use Cases |
|------|--------|-------|-----------|
| None | `0px` | `rounded-none` | **Default for everything** — buttons, inputs, cards, panels, headers, rows, sidebar elements |
| Micro | `2px` | `rounded-sm` | Provider badges, tiny status chips only |
| Full | `9999px` | `rounded-full` | Pill chips, tag badges (rare) |

**Convention:** The industrial cyberpunk aesthetic demands sharp geometry. Flat edges signal precision tooling. The only rounded elements are tiny badges and pill-shaped chips where rounding encodes "this is a discrete data token."

**Banned in all contexts:**
- `rounded-md` (6px), `rounded-lg` (8px), `rounded-xl` (12px) — these produce soft, consumer-app aesthetics
- `rounded` (4px) on buttons, inputs, or cards — use 0px
- Any border-radius on sidebar elements, data rows, or toolbar controls

---

## Opacity Tiers

Standardized opacity values used across backgrounds, borders, and text.

### Background Opacity

| Tailwind Modifier | Opacity | Use Case |
|-------------------|---------|----------|
| `/5` | 5% | Faint tints (finding highlights) |
| `/6` | 6% | Resting button background, tag chip bg |
| `/8` | 8% | Active tab, light chip, score circle bg |
| `/10` | 10% | Standard button background (outline variants) |
| `/12` | 12% | Selected/active states, template chip active |
| `/15` | 15% | Hover backgrounds, filter active state |
| `/20` | 20% | Strong hover (button bg intensification) |

### Border Opacity

| Tailwind Modifier | Opacity | Use Case |
|-------------------|---------|----------|
| `/10` | 10% | Faint borders (danger icon resting) |
| `/12` | 12% | Tag chip border |
| `/15` | 15% | Card hover borders |
| `/20` | 20% | Outline button borders, accent borders |
| `/25` | 25% | Hover border intensification |
| `/30` | 30% | Input focus borders, strong accent borders |

### Text Opacity

| Tailwind Modifier | Opacity | Use Case |
|-------------------|---------|----------|
| `/50` | 50% | Danger icon resting text, placeholder mixing |
| `/60` | 60% | Dimmed neon text (secondary task types, tag chips) |
| `/70` | 70% | Dimmed task type variants (legal, education) |
| `/80` | 80% | Near-full neon text |
| `/90` | 90% | High complexity text (neon-red) |

**Pattern:** Opacity increases on hover by one tier (e.g., `/10` bg → `/15` on hover, `/20` border → `/30` on hover).

---

## Spacing System

Ultra-compact scale (2px to 6px for most UI). Near-zero padding is the default — add space only when text becomes illegible without it. **If you can remove 2px and nothing breaks, you have too much.**

### Padding Scale

| Value | Pixels | Use Case |
|-------|--------|----------|
| `p-0.5` | 2px | Tightest: inline badges, micro-controls |
| `p-1` | 4px | Standard: card interiors, data rows, inline cards |
| `p-1.5` | 6px | **Maximum for sidebars** — panel body content, section padding |
| `p-2` | 8px | Main editor content areas ONLY (textarea, prompt well) |
| `p-2.5` | 10px | Dialog content only (rare) |

**Rule:** `p-1.5` (6px) is the ceiling for sidebar/panel content. `p-2` (8px) is only for the main editor textarea. `p-3+` is banned everywhere.

### Gap Scale

| Value | Pixels | Use Case |
|-------|--------|----------|
| `gap-0.5` | 2px | Tight data rows, dimension override lists, tab bars |
| `gap-1` | 4px | Icon + text pairs, status indicators, chip groups |
| `gap-1.5` | 6px | **Maximum for toolbars** — button groups, toolbar controls |
| `gap-2` | 8px | Dialog actions only (rare) |

### Vertical Rhythm

| Class | Pixels | Use Case |
|-------|--------|----------|
| `space-y-0.5` | 2px | Tight data row lists (scores, dimensions, stats) |
| `space-y-1` | 4px | Standard: within sections, metadata lines, sidebar items |
| `space-y-1.5` | 6px | **Maximum** — between major sections in sidebars |
| `space-y-2` | 8px | Dialog sections only (rare) |

**Rule:** `space-y-1.5` (6px) is the maximum section gap in sidebars. `space-y-2+` is banned in sidebar/panel contexts.

---

## Icon Sizing

6-tier system matching element importance.

| Size (px) | Use Cases |
|-----------|-----------|
| 10 | Inline validation icons, clear/close buttons in compact contexts |
| 12 | Button icons, action icons, chevrons, copy/edit/delete |
| 13 | Search icons, provider selector icons |
| 14 | Navigation icons, sidebar toggle, header actions |
| 16 | Large action icons, checkmarks, info/help icons |
| 24 | Empty state illustrations, hero icons |

**Convention:** Icons inherit color from their parent text class. Never hardcode icon colors — use `text-text-dim`, `text-neon-cyan`, etc.

---

## Z-Index Layering

9-layer stacking system. No arbitrary values allowed.

| Z-Index | Layer Name | Elements |
|---------|-----------|----------|
| 0 | Base | Main content, cards at rest |
| 1 | Elevated | Pipeline nodes (above connector line), sidebar cards on hover |
| 2 | Card overlay | Delete confirmation bars on sidebar cards |
| 10 | Sidebar overlay | Hover action menus on sidebar cards |
| 20 | Confirm overlay | Final delete confirmation buttons |
| 30 | Sticky | Header bar (sticky top) |
| 50 | Modal | Dialog overlays, select dropdown content, tooltip content |
| 100 | Popover | Popover content (above selects) |
| 9999 | Emergency | Skip link (accessibility focus target) |

**Convention:** Content layers (0–2) for in-flow elements. UI layers (10–30) for sticky/overlay chrome. Modal layers (50–100) for focus-trapping surfaces.

---

## Color-Mix Patterns

`color-mix(in srgb, ...)` is the primary tool for dynamic tinting — preferred over hardcoded `rgba()` when mixing with CSS variables.

### Standard Recipes

```css
/* Glass panel — 92% surface, 8% transparent */
color-mix(in srgb, var(--color-bg-secondary) 92%, transparent)

/* Semi-transparent card — 50% surface */
color-mix(in srgb, var(--color-bg-card) 50%, transparent)

/* Accent-tinted surface — 98% surface + 2% accent color */
color-mix(in srgb, var(--color-bg-card) 98%, var(--color-neon-red))

/* Depth well — mix two surfaces for inset effect */
color-mix(in srgb, var(--color-bg-primary) 60%, var(--color-bg-card))

/* Dynamic accent border (hover) — accent at 25% */
color-mix(in srgb, var(--toggle-accent, var(--color-text-dim)) 25%, transparent)

/* Dimmed placeholder text — 50% of dim color */
color-mix(in srgb, var(--color-text-dim) 50%, transparent)

/* Tag chip hierarchy — color at 6%/12%/60% for bg/border/text */
color-mix(in srgb, var(--color-neon-green) 6%, transparent)   /* background */
color-mix(in srgb, var(--color-neon-green) 12%, transparent)  /* border */
color-mix(in srgb, var(--color-neon-green) 60%, transparent)  /* text */
```

**When to use `color-mix` vs Tailwind opacity modifiers:** Use `color-mix` in CSS custom properties and `app.css` class definitions where you need to mix with CSS variables. Use Tailwind `/N` modifiers (e.g., `bg-neon-cyan/10`) in component markup for static opacity on known colors.

---

## Scrollbar

```css
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(0, 229, 255, 0.2); border-radius: 999px; }
::-webkit-scrollbar-thumb:hover { background: rgba(0, 229, 255, 0.5); }

/* Firefox */
scrollbar-width: thin;
scrollbar-color: rgba(0, 229, 255, 0.2) transparent;
```

---

## Selection

```css
::selection {
  background: rgba(0, 229, 255, 0.2);
  color: #fff;
}
```

---

## Accessibility

### Focus Rings

```css
:focus-visible {
  outline: 1px solid rgba(0, 229, 255, 0.3);
  outline-offset: 2px;
}
```

### Reduced Motion

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

### Screen Reader Support

```css
.sr-only {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border-width: 0;
}
```

### Skip Link

- Hidden off-screen by default, slides into view on focus
- Styled with `bg-card`, cyan border, 8px radius
- Focus: `top: 8px` with `outline: 2px solid rgba(0, 229, 255, 0.6)`

### Contrast Compliance

| Foreground | Background | Approximate Ratio | WCAG Level |
|-----------|------------|-------------------|------------|
| text-primary (#e4e4f0) | bg-primary (#06060c) | 15:1 | AAA |
| text-secondary (#8b8ba8) | bg-primary (#06060c) | 7:1 | AAA |
| text-dim (#7a7a9e) | bg-primary (#06060c) | 5.5:1 | AA |
| neon-cyan (#00e5ff) | bg-primary (#06060c) | 11:1 | AAA |
| neon-green (#22ff88) | bg-primary (#06060c) | 12:1 | AAA |
| neon-red (#ff3366) | bg-primary (#06060c) | 6:1 | AA |

All primary text meets WCAG AAA. Neon accent text meets AA minimum. Dim/metadata text meets AA.
