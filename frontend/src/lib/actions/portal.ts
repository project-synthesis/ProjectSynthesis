import type { Action } from 'svelte/action';

/**
 * Svelte action that teleports its host element to document.body.
 *
 * Motivation: Navigator.svelte's `animate-fade-in` container ends with
 * `transform: translateY(0)` via fill-mode:both. Per the CSS spec, any
 * non-`none` transform on an ancestor makes it the containing block for
 * `position:fixed` descendants — so without this portal, the modal's
 * fixed positioning is relative to the navigator column instead of
 * the viewport.
 *
 * Usage:
 *   <div use:portal>
 *     <div class="fixed inset-0 z-50">backdrop</div>
 *     <div class="fixed ...">modal</div>
 *   </div>
 */
export const portal: Action<HTMLElement> = (node) => {
  // Guard against SSR — actions only execute in the browser.
  if (typeof document === 'undefined') return;

  document.body.appendChild(node);

  return {
    destroy() {
      node.remove();
    },
  };
};
