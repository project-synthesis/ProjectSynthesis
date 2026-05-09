/**
 * Shared copy-flash helper.
 *
 * Pre-2026-05-09 four components hand-rolled the "show 'Copied' for N
 * milliseconds, then reset" pattern with three different durations:
 *   - Logo.svelte                    → 1200ms isAnimating
 *   - PassthroughView.svelte         → 1200ms copy-state
 *   - editor/ForgeArtifact.svelte    → 2000ms copied flag
 *   - landing/CodeBlock.svelte       → 1500ms copyLabel reset
 *
 * Users saw the same logical operation (copy-to-clipboard) reset on
 * different cadences depending on which surface they were looking at.
 * This helper centralizes the timing on ``--duration-copy-flash``
 * (defined in app.css) and exposes a Svelte-5-compatible reactive
 * primitive — consumers read ``triggered`` as a $state-tracked getter
 * and call ``trigger()`` after a successful copy.
 *
 * The helper deliberately avoids reading getComputedStyle at call time
 * (jsdom-fragile, and the duration is conceptually a constant). The
 * duration is mirrored from the CSS var here; if the CSS var changes,
 * this constant must follow. A regression test pins the relationship.
 *
 * Copyright 2026 Project Synthesis contributors.
 */

/** Source of truth for the copy-flash window. Mirrors
 *  ``--duration-copy-flash`` in app.css. Exported so consumers (and
 *  tests) can reference the same value. */
export const COPY_FLASH_DURATION_MS = 1500;

export interface CopyFlashHandle {
  /** True for ``COPY_FLASH_DURATION_MS`` after the most recent
   *  ``trigger()`` call; false otherwise. */
  readonly triggered: boolean;
  /** Begin (or restart) the flash window. Restarting clears any prior
   *  pending reset so the visible state stays "Copied" for a fresh
   *  COPY_FLASH_DURATION_MS rather than terminating early. */
  trigger(): void;
}

/**
 * Reactive copy-flash primitive for Svelte 5.
 *
 * Usage:
 *   const flash = useCopyFlash();
 *   ...
 *   <button onclick={() => { copy(value); flash.trigger(); }}>
 *     {flash.triggered ? 'Copied' : 'Copy'}
 *   </button>
 *
 * MUST be called from a `.svelte`/`.svelte.ts` module — uses `$state`.
 *
 * @param onReset Optional callback invoked when the flash window
 *   expires. Receives no arguments. Useful when consumers need to
 *   trigger downstream effects (e.g. blur the trigger element).
 */
export function useCopyFlash(onReset?: () => void): CopyFlashHandle {
  let _triggered = $state(false);
  let _timer: ReturnType<typeof setTimeout> | null = null;

  function _clear(): void {
    if (_timer != null) {
      clearTimeout(_timer);
      _timer = null;
    }
  }

  return {
    get triggered(): boolean {
      return _triggered;
    },
    trigger(): void {
      _clear();
      _triggered = true;
      _timer = setTimeout(() => {
        _triggered = false;
        _timer = null;
        onReset?.();
      }, COPY_FLASH_DURATION_MS);
    },
  };
}
