# Color Mappings Reference

## Table of Contents
- [Strategy Color Mapping](#strategy-color-mapping)
- [Task Type Color Mapping](#task-type-color-mapping)
- [Complexity Colors](#complexity-colors)
- [Score-to-Color Mapping](#score-to-color-mapping)
- [Data Visualization](#data-visualization)

---

## Strategy Color Mapping

Each of the 10 optimization strategies has a unique neon color for visual identification across bars, badges, borders, and buttons.

| Strategy | Color Name | Hex | Raw RGBA |
|----------|-----------|-----|----------|
| Chain of Thought | neon-cyan | `#00e5ff` | `rgba(0, 229, 255, 0.35)` |
| CO-STAR | neon-purple | `#a855f7` | `rgba(168, 85, 247, 0.35)` |
| RISEN | neon-green | `#22ff88` | `rgba(34, 255, 136, 0.35)` |
| Role-Task-Format | neon-red | `#ff3366` | `rgba(255, 51, 102, 0.35)` |
| Few-Shot | neon-yellow | `#fbbf24` | `rgba(251, 191, 36, 0.35)` |
| Step by Step | neon-orange | `#ff8c00` | `rgba(255, 140, 0, 0.35)` |
| Structured Output | neon-blue | `#4d8eff` | `rgba(77, 142, 255, 0.35)` |
| Constraint Injection | neon-pink | `#ff6eb4` | `rgba(255, 110, 180, 0.35)` |
| Context Enrichment | neon-teal | `#00d4aa` | `rgba(0, 212, 170, 0.35)` |
| Persona Assignment | neon-indigo | `#7b61ff` | `rgba(123, 97, 255, 0.35)` |

**Source:** `frontend/src/lib/utils/strategies.ts` (`STRATEGY_COLOR_META`)

---

## Task Type Color Mapping

Each of the 14 classified task types has a neon color assignment. Primary types use full-opacity colors; secondary types use dimmed variants.

| Task Type | Color Name | CSS Color | Raw RGBA |
|-----------|-----------|-----------|----------|
| coding | neon-cyan | `#00e5ff` | `rgba(0, 229, 255, 0.35)` |
| analysis | neon-blue | `#4d8eff` | `rgba(77, 142, 255, 0.35)` |
| reasoning | neon-indigo | `#7b61ff` | `rgba(123, 97, 255, 0.35)` |
| math | neon-purple | `#a855f7` | `rgba(168, 85, 247, 0.35)` |
| writing | neon-green | `#22ff88` | `rgba(34, 255, 136, 0.35)` |
| creative | neon-pink | `#ff6eb4` | `rgba(255, 110, 180, 0.35)` |
| extraction | neon-teal | `#00d4aa` | `rgba(0, 212, 170, 0.35)` |
| classification | neon-orange | `#ff8c00` | `rgba(255, 140, 0, 0.35)` |
| formatting | neon-yellow | `#fbbf24` | `rgba(251, 191, 36, 0.35)` |
| medical | neon-red | `#ff3366` | `rgba(255, 51, 102, 0.35)` |
| legal | neon-red (dim) | `rgba(255, 51, 102, 0.7)` | `rgba(255, 51, 102, 0.25)` |
| education | neon-teal (dim) | `rgba(0, 212, 170, 0.7)` | `rgba(0, 212, 170, 0.25)` |
| general | neon-cyan (dim) | `rgba(0, 229, 255, 0.6)` | `rgba(0, 229, 255, 0.20)` |
| other | text-dim | `rgba(255, 255, 255, 0.4)` | `rgba(255, 255, 255, 0.10)` |

**Source:** `frontend/src/lib/utils/taskTypes.ts` (`TASK_TYPE_COLOR_META`)

---

## Complexity Colors

3-tier system with alias normalization (simple/low, moderate/medium, complex/high).

| Level | Aliases | Color Name | Hex | Raw RGBA |
|-------|---------|-----------|-----|----------|
| Low | simple, low | neon-green | `#22ff88` | `rgba(34, 255, 136, 0.35)` |
| Medium | moderate, medium | neon-yellow | `#fbbf24` | `rgba(251, 191, 36, 0.35)` |
| High | complex, high | neon-red | `#ff3366` | `rgba(255, 51, 102, 0.35)` |

**Source:** `frontend/src/lib/utils/complexity.ts`

---

## Score-to-Color Mapping

| Range | Color | Semantic |
|-------|-------|----------|
| 1–3 | neon-red | Poor — needs significant improvement |
| 4–6 | neon-yellow | Moderate — room for improvement |
| 7–8 | neon-cyan | Good — solid quality |
| 9–10 | neon-green | Excellent — high precision |

---

## Data Visualization

### Score Display Conventions

| Context | Format | Font | Color Logic |
|---------|--------|------|-------------|
| Score circle | Integer 1–10 inside 20px circle | Mono, 10px, 700 | Score-mapped (see table above) |
| Inline score | `8.2/10` | Mono, inherited size | Accent color of parent context |
| Score delta | `+2.1` or `-0.8` | Mono, inherited size | Positive = green, negative = red, zero = dim |
| Percentage | `42%` | Mono, inherited size | Contextual accent |
| Confidence | `0.87` (0–1 scale) | Mono, inherited size | Strategy accent color |

### Comparative Display

When showing before/after or side-by-side results:
- Original prompt: `text-secondary` (dimmed, the "before")
- Optimized prompt: `text-primary` (bright, the "after")
- Score improvements: green delta badges (`+N.N`) next to the optimized score
- Score regressions: red delta badges (`-N.N`) — never hidden, always visible
- Equal scores: dim text, no badge

### Progress Indicators

| Type | Visual | Animation |
|------|--------|-----------|
| Pipeline stage progress | 4-node vertical timeline with gradient connectors | Nodes fill with stage color as they complete |
| Score bar | Horizontal fill bar, width = score/10 | 500ms `ease` fill animation |
| Batch progress | Fraction counter (`3/12`) in mono | Instant update, no animation on count |
| Loading skeleton | `shimmer` gradient sweep on bg-hover surface | 1500ms infinite |
