/**
 * Branded tooltip action — replaces native title attributes with styled overlays.
 *
 * Usage: <span use:tooltip={'Score spread — low = consistent, high = variable'}>0.42</span>
 *
 * Renders a positioned overlay appended to document.body (avoids overflow clipping).
 * Brand: dark bg, 1px neon-cyan contour, monospace font, sharp corners, no glow.
 */

const SHOW_DELAY_MS = 400;
const HIDE_DELAY_MS = 0;
const OFFSET_Y = 8;

let activeTooltip: HTMLDivElement | null = null;
let showTimer: ReturnType<typeof setTimeout> | null = null;

function createTooltipElement(): HTMLDivElement {
  const el = document.createElement('div');
  el.className = 'synthesis-tooltip';
  el.setAttribute('role', 'tooltip');

  // Inline styles for portability — no external CSS dependency.
  // Matches brand: bg-card, 1px neon-cyan border, mono font, sharp corners.
  Object.assign(el.style, {
    position: 'fixed',
    zIndex: '9999',
    padding: '4px 8px',
    background: '#11111e',
    border: '1px solid rgba(0, 229, 255, 0.35)',
    color: '#8b8ba8',
    fontFamily: "'Geist Mono', 'JetBrains Mono', ui-monospace, monospace",
    fontSize: '10px',
    lineHeight: '1.4',
    maxWidth: '280px',
    pointerEvents: 'none',
    whiteSpace: 'pre-line',
    opacity: '0',
    transition: 'opacity 150ms cubic-bezier(0.16, 1, 0.3, 1)',
  });

  return el;
}

function positionTooltip(el: HTMLDivElement, anchor: HTMLElement): void {
  const rect = anchor.getBoundingClientRect();
  const tooltipRect = el.getBoundingClientRect();

  // Default: below and centered
  let left = rect.left + (rect.width - tooltipRect.width) / 2;
  let top = rect.bottom + OFFSET_Y;

  // Clamp horizontal to viewport
  const margin = 8;
  if (left < margin) left = margin;
  if (left + tooltipRect.width > window.innerWidth - margin) {
    left = window.innerWidth - margin - tooltipRect.width;
  }

  // Flip above if below viewport
  if (top + tooltipRect.height > window.innerHeight - margin) {
    top = rect.top - tooltipRect.height - OFFSET_Y;
  }

  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
}

function show(anchor: HTMLElement, text: string): void {
  hide(); // clean up any existing

  const el = createTooltipElement();
  el.textContent = text;
  document.body.appendChild(el);
  activeTooltip = el;

  // Position after append (needs layout to measure)
  requestAnimationFrame(() => {
    if (activeTooltip === el) {
      positionTooltip(el, anchor);
      el.style.opacity = '1';
    }
  });
}

function hide(): void {
  if (showTimer) {
    clearTimeout(showTimer);
    showTimer = null;
  }
  if (activeTooltip) {
    activeTooltip.remove();
    activeTooltip = null;
  }
}

/**
 * Svelte action: branded tooltip on hover.
 *
 * ```svelte
 * <span use:tooltip={'Intra-cluster similarity (0–1)'}>0.976</span>
 * ```
 */
export function tooltip(node: HTMLElement, text: string | null | undefined) {
  // Remove any native title to prevent double-tooltip
  if (node.hasAttribute('title')) {
    node.removeAttribute('title');
  }

  let currentText = text;

  function onEnter() {
    if (!currentText) return;
    const t = currentText; // capture for closure
    showTimer = setTimeout(() => show(node, t), SHOW_DELAY_MS);
  }

  function onLeave() {
    hide();
  }

  node.addEventListener('mouseenter', onEnter);
  node.addEventListener('mouseleave', onLeave);
  node.addEventListener('focus', onEnter);
  node.addEventListener('blur', onLeave);

  return {
    update(newText: string | null | undefined) {
      currentText = newText;
      // If tooltip is currently showing for this node, update it
      if (activeTooltip && showTimer === null) {
        activeTooltip.textContent = newText ?? '';
      }
    },
    destroy() {
      hide();
      node.removeEventListener('mouseenter', onEnter);
      node.removeEventListener('mouseleave', onLeave);
      node.removeEventListener('focus', onEnter);
      node.removeEventListener('blur', onLeave);
    },
  };
}
