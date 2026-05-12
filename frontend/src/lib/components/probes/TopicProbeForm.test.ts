// frontend/src/lib/components/probes/TopicProbeForm.test.ts
//
// Cycle 11 RED — Topic Probe form contract.
//
// The form drives the user-facing inputs for `POST /api/probes`:
//   - topic textarea (3–500 char validation)
//   - n_prompts slider (5–25 clamp)
//   - intent dropdown (4–5 options)
//   - grounding_mode segmented control ('codebase' default, 'topic_only')
//   - submit button gated by linked-repo state + grounding selection
//
// These tests pin the contract before the component exists. All assertions
// describe behavior, not pixel-perfect visuals; brand checks live in
// the cycle-14 audit grep.
//
// Each test loads the component via a dynamic import so the test runner
// can collect + report on each `it()` even while the file is missing in
// RED state. Static imports here would fail at suite-collection time.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

// Stub the github store — the form reads `linkedRepo` to decide whether the
// 'codebase' grounding mode is even selectable. Tests re-assign the value
// per-case.
const githubMock = vi.hoisted(() => ({
  githubStore: {
    linkedRepo: null as null | { full_name: string },
  },
}));
vi.mock('$lib/stores/github.svelte', () => githubMock);

/**
 * Dynamic loader so each test can fail with a clear missing-module
 * message rather than crashing collection. The runtime-computed path
 * keeps Vite's static analyzer from resolving the import at suite-
 * collection time — the import is deferred to the actual test run, so
 * individual tests fail with module-not-found rather than the entire
 * suite crashing on import (RED behavior).
 */
async function loadComponent() {
  const path = ['.', 'TopicProbeForm.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

describe('TopicProbeForm', () => {
  beforeEach(() => {
    githubMock.githubStore.linkedRepo = null;
  });
  afterEach(() => {
    cleanup();
  });

  // ── Test 1: topic textarea validation ──────────────────────────────
  it('test_topic_textarea_validation_3_to_500_chars', async () => {
    githubMock.githubStore.linkedRepo = { full_name: 'octocat/demo' };
    const user = userEvent.setup();
    const TopicProbeForm = await loadComponent();
    render(TopicProbeForm, { props: { onSubmit: vi.fn() } });

    const textarea = screen.getByLabelText(/topic/i) as HTMLTextAreaElement;
    const submit = screen.getByRole('button', { name: /run probe/i });

    // Empty → submit disabled.
    expect(submit).toBeDisabled();

    // 2 chars ('ab') → still disabled (below 3-char floor).
    await user.type(textarea, 'ab');
    expect(submit).toBeDisabled();

    // 3 chars ('abc') → enabled.
    await user.type(textarea, 'c');
    expect(submit).not.toBeDisabled();

    // 500-char input → enabled.
    await user.clear(textarea);
    textarea.value = 'a'.repeat(500);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    expect(submit).not.toBeDisabled();

    // 501-char input → disabled (exceeds ceiling).
    textarea.value = 'a'.repeat(501);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    expect(submit).toBeDisabled();
  });

  // ── Test 2: n_prompts slider 5–25 clamp ────────────────────────────
  it('test_n_slider_5_to_25', async () => {
    githubMock.githubStore.linkedRepo = { full_name: 'octocat/demo' };
    const TopicProbeForm = await loadComponent();
    render(TopicProbeForm, { props: { onSubmit: vi.fn() } });

    const slider = screen.getByLabelText(
      /prompts|number of prompts|n[_\s-]*prompts/i,
    ) as HTMLInputElement;

    // Slider declares min=5 and max=25 — the HTMLInputElement constraint
    // attributes are the contract surface, browsers + range pickers will
    // honor them.
    expect(slider.min).toBe('5');
    expect(slider.max).toBe('25');

    // Below floor → clamped to 5.
    slider.value = '4';
    slider.dispatchEvent(new Event('input', { bubbles: true }));
    expect(Number(slider.value)).toBeGreaterThanOrEqual(5);

    // Above ceiling → clamped to 25.
    slider.value = '26';
    slider.dispatchEvent(new Event('input', { bubbles: true }));
    expect(Number(slider.value)).toBeLessThanOrEqual(25);

    // In-range value → accepted as-is.
    slider.value = '10';
    slider.dispatchEvent(new Event('input', { bubbles: true }));
    expect(Number(slider.value)).toBe(10);
  });

  // ── Test 3: intent dropdown ────────────────────────────────────────
  it('test_intent_dropdown', async () => {
    githubMock.githubStore.linkedRepo = { full_name: 'octocat/demo' };
    const TopicProbeForm = await loadComponent();
    render(TopicProbeForm, { props: { onSubmit: vi.fn() } });

    // The intent dropdown is a <select> with at least 4 options
    // (explore/audit/refactor/+) per spec; default selected on mount.
    const select = screen.getByLabelText(/intent/i) as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.options.length).toBeGreaterThanOrEqual(4);
    expect(select.options.length).toBeLessThanOrEqual(5);

    // Default selection — at least one option is the active value.
    expect(select.value).not.toBe('');

    // Canonical intent vocabulary per spec §6 voice — at minimum the
    // three canonical intents appear in the dropdown.
    const values = Array.from(select.options).map((o) => o.value);
    const labels = Array.from(select.options).map(
      (o) => o.textContent?.trim().toLowerCase() ?? '',
    );
    const haystack = [...values, ...labels].join(' ').toLowerCase();
    expect(haystack).toMatch(/explore/);
    expect(haystack).toMatch(/audit/);
    expect(haystack).toMatch(/refactor/);
  });

  // ── Test 4: grounding mode segmented control ───────────────────────
  it('test_grounding_mode_segmented_control', async () => {
    // No linked repo to start — 'codebase' must be disabled, 'topic_only'
    // enabled. The control is a two-segment toggle group.
    githubMock.githubStore.linkedRepo = null;
    const user = userEvent.setup();
    const TopicProbeForm = await loadComponent();
    render(TopicProbeForm, { props: { onSubmit: vi.fn() } });

    const codebaseBtn = screen.getByRole('radio', { name: /codebase/i });
    const topicOnlyBtn = screen.getByRole('radio', { name: /topic[_\s-]*only/i });

    expect(codebaseBtn).toBeInTheDocument();
    expect(topicOnlyBtn).toBeInTheDocument();

    // Without a linked repo, 'codebase' is disabled.
    expect(codebaseBtn).toBeDisabled();

    // After clicking topic_only the control reflects the selection.
    await user.click(topicOnlyBtn);
    expect(topicOnlyBtn).toBeChecked();
    expect(codebaseBtn).not.toBeChecked();

    // Now link a repo + re-render — codebase becomes selectable and is
    // the default.
    cleanup();
    githubMock.githubStore.linkedRepo = { full_name: 'octocat/demo' };
    render(TopicProbeForm, { props: { onSubmit: vi.fn() } });
    const codebaseBtn2 = screen.getByRole('radio', { name: /codebase/i });
    expect(codebaseBtn2).not.toBeDisabled();
    // Default = codebase when repo is linked.
    expect(codebaseBtn2).toBeChecked();
  });

  // ── Test 5: submit button gated by linked repo + grounding mode ─────
  it('test_submit_button_state_per_linked_repo', async () => {
    githubMock.githubStore.linkedRepo = null;
    const user = userEvent.setup();
    const TopicProbeForm = await loadComponent();
    const { rerender } = render(TopicProbeForm, {
      props: { onSubmit: vi.fn() },
    });

    const textarea = screen.getByLabelText(/topic/i) as HTMLTextAreaElement;
    await user.type(textarea, 'embedding cache invalidation');

    // Without repo + 'codebase' default → submit disabled.
    let submit = screen.getByRole('button', { name: /run probe/i });
    expect(submit).toBeDisabled();

    // Switch to 'topic_only' → submit enabled (no repo required).
    const topicOnlyBtn = screen.getByRole('radio', { name: /topic[_\s-]*only/i });
    await user.click(topicOnlyBtn);
    submit = screen.getByRole('button', { name: /run probe/i });
    expect(submit).not.toBeDisabled();

    // Now link a repo + re-render → submit enabled with 'codebase'.
    githubMock.githubStore.linkedRepo = { full_name: 'octocat/demo' };
    await rerender({ onSubmit: vi.fn() });
    submit = screen.getByRole('button', { name: /run probe/i });
    expect(submit).not.toBeDisabled();
  });
});
