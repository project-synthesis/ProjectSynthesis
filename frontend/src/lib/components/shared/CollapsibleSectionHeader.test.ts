/**
 * CollapsibleSectionHeader — behavior + brand compliance contract tests.
 *
 * Coverage:
 *  - whole-bar mode dispatches onToggle on bar click
 *  - actions snippet click does NOT bubble as toggle (stopPropagation boundary)
 *  - split mode caret dispatches onToggle, header snippet click does NOT
 *  - ARIA labels present + aria-expanded mirrors `open` prop
 *  - no glow/shadow/drop-shadow — brand guard
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import Harness from './CollapsibleSectionHeader.test.svelte';

/** Brand guard — same assertion used by readiness tests. */
function assertNoGlowShadow(container: HTMLElement): void {
  const all = container.querySelectorAll('[style]');
  for (const el of all) {
    const style = el.getAttribute('style') ?? '';
    if (/box-shadow:/i.test(style)) {
      expect(style).toMatch(/inset\s+0\s+0\s+0\s+\d+px/);
    }
    expect(style).not.toMatch(/text-shadow:/i);
    expect(style).not.toMatch(/filter:\s*drop-shadow/i);
  }
}

describe('CollapsibleSectionHeader — whole-bar mode', () => {
  afterEach(() => cleanup());

  it('fires onToggle when the toggle bar is clicked', async () => {
    const onToggle = vi.fn();
    const { container } = render(Harness, {
      props: { open: true, onToggle, mode: 'whole', label: 'Templates' },
    });
    const btn = container.querySelector('.nsh-toggle') as HTMLElement;
    expect(btn).toBeInTheDocument();
    await fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('renders label and count with correct brand typography classes', () => {
    const { container } = render(Harness, {
      props: { open: true, onToggle: () => {}, mode: 'whole', label: 'Domain Readiness', count: 7 },
    });
    expect(container.querySelector('.nsh-label')?.textContent?.trim()).toBe('Domain Readiness');
    expect(container.querySelector('.nsh-count')?.textContent?.trim()).toBe('7');
  });

  it('renders caret ▾ when open, ▸ when collapsed', () => {
    const openRender = render(Harness, {
      props: { open: true, onToggle: () => {}, mode: 'whole', label: 'X' },
    });
    expect(openRender.container.querySelector('.nsh-caret')?.textContent?.trim()).toBe('▾');
    openRender.unmount();

    const closedRender = render(Harness, {
      props: { open: false, onToggle: () => {}, mode: 'whole', label: 'X' },
    });
    expect(closedRender.container.querySelector('.nsh-caret')?.textContent?.trim()).toBe('▸');
  });

  it('exposes aria-expanded mirroring open prop', () => {
    const { container } = render(Harness, {
      props: { open: false, onToggle: () => {}, mode: 'whole', label: 'X' },
    });
    const btn = container.querySelector('.nsh-toggle') as HTMLElement;
    expect(btn.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('CollapsibleSectionHeader — actions snippet', () => {
  afterEach(() => cleanup());

  it('action click does NOT fire onToggle (stopPropagation boundary)', async () => {
    const onToggle = vi.fn();
    const actionClick = vi.fn();
    const { container } = render(Harness, {
      props: {
        open: true,
        onToggle,
        mode: 'actions',
        label: 'Readiness',
        actionClick,
      },
    });
    const action = container.querySelector('.test-action-btn') as HTMLElement;
    expect(action).toBeInTheDocument();
    await fireEvent.click(action);
    expect(actionClick).toHaveBeenCalledTimes(1);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('toggle still fires when the main bar area is clicked', async () => {
    const onToggle = vi.fn();
    const { container } = render(Harness, {
      props: { open: true, onToggle, mode: 'actions', label: 'X' },
    });
    const btn = container.querySelector('.nsh-toggle') as HTMLElement;
    await fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe('CollapsibleSectionHeader — split mode', () => {
  afterEach(() => cleanup());

  it('caret button dispatches onToggle', async () => {
    const onToggle = vi.fn();
    const { container } = render(Harness, {
      props: { open: true, onToggle, mode: 'split', label: 'Backend' },
    });
    const caretBtn = container.querySelector('.nsh-caret-btn') as HTMLElement;
    expect(caretBtn).toBeInTheDocument();
    await fireEvent.click(caretBtn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('header snippet click does NOT bubble as toggle', async () => {
    const onToggle = vi.fn();
    const headerClick = vi.fn();
    const { container } = render(Harness, {
      props: { open: true, onToggle, mode: 'split', label: 'Backend', headerClick },
    });
    const headerBtn = container.querySelector('.test-header-btn') as HTMLElement;
    expect(headerBtn).toBeInTheDocument();
    await fireEvent.click(headerBtn);
    expect(headerClick).toHaveBeenCalledTimes(1);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('provides a discrete aria-labeled caret button', () => {
    const { container } = render(Harness, {
      props: { open: false, onToggle: () => {}, mode: 'split', label: 'Backend' },
    });
    const caretBtn = container.querySelector('.nsh-caret-btn') as HTMLElement;
    expect(caretBtn.getAttribute('aria-label')).toBe('Toggle section');
    expect(caretBtn.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('CollapsibleSectionHeader — brand compliance', () => {
  afterEach(() => cleanup());

  it('contains no glow, drop-shadow, or text-shadow in any mode', () => {
    const whole = render(Harness, {
      props: { open: true, onToggle: () => {}, mode: 'whole', label: 'X', count: 3 },
    });
    assertNoGlowShadow(whole.container);
    whole.unmount();

    const actions = render(Harness, {
      props: { open: true, onToggle: () => {}, mode: 'actions', label: 'X' },
    });
    assertNoGlowShadow(actions.container);
    actions.unmount();

    const split = render(Harness, {
      props: { open: true, onToggle: () => {}, mode: 'split', label: 'X' },
    });
    assertNoGlowShadow(split.container);
  });
});
