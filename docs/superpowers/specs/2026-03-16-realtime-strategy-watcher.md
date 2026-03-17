# Real-Time Strategy File Watching

## Problem

Strategy files are fully modular (filesystem-driven), but changes require a manual page refresh to appear in the UI. Adding, editing, or deleting `.md` files in `prompts/strategies/` has no instant feedback.

## Solution

OS-native file watching via `watchfiles.awatch()` + event bus + SSE + toast notifications. Changes propagate to the UI in <600ms (500ms debounce + network) with visual feedback.

---

## 1. Backend: File Watcher Service

**File**: `backend/app/services/file_watcher.py`

Watches `prompts/strategies/` using `watchfiles.awatch()` (inotify on Linux, FSEvents on macOS). The `awatch()` iterator yields batched change sets.

### Behavior

- Runs as a background `asyncio.Task` created in the FastAPI lifespan
- Uses `watchfiles.awatch(path, debounce=500)` — overrides the 1600ms default to 500ms. No custom debounce layer needed; `awatch()` batches all changes within the debounce window into a single yielded set.
- Classifies changes by comparing the yielded `(Change, path)` tuples: `Change.added`, `Change.modified`, `Change.deleted`
- Publishes one `strategy_changed` event per affected file to `event_bus`
- Gracefully cancels on shutdown via `task.cancel()`

### Event Payload

```json
{
  "action": "created" | "modified" | "deleted",
  "name": "chain-of-thought",
  "timestamp": 1710582400.0
}
```

### Error Resilience

- If `awatch()` raises an exception: catch it, log warning, restart `awatch()` with `force_polling=True, poll_delay_ms=2000` (built-in polling fallback, no custom loop)
- Missing directory: watcher logs info and exits cleanly (no crash)
- Permission errors: logged, watcher continues

### Dependency

Add `watchfiles>=1.0.0` to `backend/requirements.txt` as an explicit dependency (currently installed transitively via uvicorn, but must be declared for CI/fresh installs).

---

## 2. Lifespan Integration

**File**: `backend/app/main.py`

Start watcher task before `yield`, cancel after `yield`:

```python
watcher_task = asyncio.create_task(watch_strategy_files(PROMPTS_DIR / "strategies"))
app.state.watcher_task = watcher_task
# ... yield ...
app.state.watcher_task.cancel()
```

---

## 3. SSE Transport

**File**: `frontend/src/lib/api/client.ts`

Add `strategy_changed` to the `eventTypes` array in `connectEventStream()`.

**File**: `frontend/src/routes/+page.svelte`

Dispatch `strategy-changed` custom window event when SSE `strategy_changed` fires. Pass the full data payload (`{action, name, timestamp}`).

---

## 4. Toast Store + Component

### Toast Store

**File**: `frontend/src/lib/stores/toast.svelte.ts`

A reactive store that manages the toast queue. This is the API contract — all consumers import `addToast()` from this store.

```typescript
interface ToastItem {
  id: string;
  symbol: string;   // "+", "~", "-"
  message: string;  // "chain-of-thought detected"
  color: string;    // CSS variable name
}

class ToastStore {
  toasts = $state<ToastItem[]>([]);

  addToast(action: string, name: string): void;  // push + auto-dismiss
  dismiss(id: string): void;                      // remove by id
}

export const toastStore = new ToastStore();
export function addToast(action: string, name: string): void;
```

**addToast behavior:**
- Maps action to symbol/color: created → `+`/neon-green, modified → `~`/neon-yellow, deleted → `-`/neon-red
- Generates unique ID (timestamp + counter)
- Pushes to `toasts` array
- Sets 3-second `setTimeout` for auto-dismiss
- If `toasts.length > 3`, dismisses oldest

### Toast Component

**File**: `frontend/src/lib/components/shared/Toast.svelte`

Reads from `toastStore.toasts` and renders the visual notification stack.

### Visual Spec (Brand-Compliant)

| Property | Value | Rationale |
|----------|-------|-----------|
| Position | fixed, bottom-right, 8px from edge | Non-blocking, visible |
| z-index | 50 | Modal layer per brand z-index system |
| Background | `var(--color-bg-card)` | Elevated surface |
| Border | 1px `var(--color-border-subtle)` | Neon tube model |
| Border-radius | 0px | Industrial flat edges |
| Height | 22px | Compact, matches status bar density |
| Padding | `0 8px` | Minimal |
| Font | `var(--font-mono)`, 10px | Data/system notification |
| Text color | `var(--color-text-dim)` for message | Dim default |
| Box-shadow | None | Zero-effects directive |

### Action Colors (Chromatic Encoding)

| Action | Symbol | Color | Semantic mapping |
|--------|--------|-------|-----------------|
| Created | `+` | `var(--color-neon-green)` | Health/success |
| Modified | `~` | `var(--color-neon-yellow)` | Warning/attention |
| Deleted | `-` | `var(--color-neon-red)` | Danger/destruction |

### Animation

- **Entrance**: `translateY(8px) → translateY(0)`, opacity 0→1, 300ms spring easing `cubic-bezier(0.16, 1, 0.3, 1)`
- **Exit**: opacity 1→0, 200ms accelerating easing `cubic-bezier(0.4, 0, 1, 1)`
- **Respects** `prefers-reduced-motion` → 0.01ms duration

### Behavior

- Auto-dismiss after 3 seconds
- Stack vertically with 4px gap
- Max 3 visible (oldest dismissed when 4th arrives)
- No close button (auto-dismiss only — minimal chrome)

### Toast Messages

| Action | Message |
|--------|---------|
| Created | `+ chain-of-thought detected` |
| Modified | `~ chain-of-thought updated` |
| Deleted | `- chain-of-thought removed` |

---

## 5. Frontend Reactivity

### Double-Notification Suppression

When the user saves a strategy via the inline editor (PUT /api/strategies), the watcher will also detect the disk change and fire `strategy_changed`. To prevent a redundant toast:

**Frontend suppression (Navigator.svelte)**: After a successful `saveStrategyEdit()`, set a 2-second suppression flag for that strategy name. When `strategy-changed` window event arrives, check the suppression set — if the name is suppressed, skip the toast (the re-fetch is also unnecessary since `saveStrategyEdit` already refreshed the list).

```typescript
let suppressedNames = $state<Set<string>>(new Set());

async function saveStrategyEdit() {
  // ... save ...
  suppressedNames.add(editingStrategy);
  setTimeout(() => suppressedNames.delete(editingStrategy), 2000);
}

// In strategy-changed handler:
if (suppressedNames.has(detail.name)) return; // skip toast + re-fetch
```

### Navigator Strategy Panel

Listen for `strategy-changed` window event → if not suppressed → re-fetch `GET /api/strategies` → strategy list auto-updates → show toast via `addToast(action, name)`.

If user is editing a strategy that was externally modified, the edit content stays (no clobber) but the list refreshes around it.

### PromptEdit Toolbar Dropdown

Same listener → re-fetch strategies → dropdown options update.

**Deleted-strategy reset**: After re-fetching, if `forgeStore.strategy !== null && !newStrategies.some(s => s.name === forgeStore.strategy)`, then set `forgeStore.strategy = null`.

**Auto always present**: The dropdown always shows "auto" as the first option (hardcoded `{ value: '', label: 'auto' }`), regardless of whether `auto.md` exists on disk. The Navigator sidebar gets its list from the API, so if `auto.md` is deleted, it disappears from the sidebar but the dropdown retains the hardcoded auto option.

---

## 6. Files to Create/Modify

| File | Action |
|------|--------|
| `backend/app/services/file_watcher.py` | **Create** — strategy file watcher using watchfiles.awatch() |
| `backend/app/main.py` | **Modify** — start/stop watcher in lifespan |
| `backend/requirements.txt` | **Modify** — add `watchfiles>=1.0.0` explicit dependency |
| `frontend/src/lib/stores/toast.svelte.ts` | **Create** — toast store (addToast API) |
| `frontend/src/lib/components/shared/Toast.svelte` | **Create** — toast notification component |
| `frontend/src/lib/api/client.ts` | **Modify** — add `strategy_changed` to SSE types |
| `frontend/src/routes/+page.svelte` | **Modify** — dispatch `strategy-changed` window event |
| `frontend/src/lib/components/layout/Navigator.svelte` | **Modify** — listen for changes, suppression, show toasts |
| `frontend/src/lib/components/editor/PromptEdit.svelte` | **Modify** — listen for changes, refresh dropdown, reset deleted strategy |
| `frontend/src/routes/+layout.svelte` | **Modify** — mount Toast container |

---

## 7. Verification

### Happy path
1. Start server → add a new `.md` file to `prompts/strategies/` → toast appears within 1s, sidebar + dropdown update
2. Edit an existing `.md` file → toast shows "updated", content refreshes
3. Delete a `.md` file → toast shows "removed", entry disappears from sidebar + dropdown
4. Delete all `.md` files → sidebar shows empty state, dropdown retains hardcoded "auto"

### Edge cases
5. Rapid edits (save 5 times in 1 second) → single debounced batch, one toast per affected file
6. Watcher error (chmod 000 on directory) → logs warning, restarts with force_polling, no crash
7. Server restart → watcher restarts cleanly
8. Edit via UI (PUT /api/strategies) → watcher detects disk change → suppressed (no duplicate toast)
9. Delete the strategy that's currently active in forgeStore → strategy resets to null (auto), dropdown shows auto
10. Edit a strategy while it's being externally modified → edit content preserved, list refreshes around it
