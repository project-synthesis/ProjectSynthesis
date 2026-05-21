// frontend/src/lib/components/layout/RunActionMenu.test.ts
// v0.4.32 — kebab popover ESC close behavior.
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import RunActionMenu from './RunActionMenu.svelte';

describe('RunActionMenu', () => {
  it('Test 19: ESC key closes menu (calls onClose)', async () => {
    const onClose = vi.fn();
    const onRename = vi.fn();
    const onDelete = vi.fn();
    render(RunActionMenu, { onRename, onDelete, onClose });
    await fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
