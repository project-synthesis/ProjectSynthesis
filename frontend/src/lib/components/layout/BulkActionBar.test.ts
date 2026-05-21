// frontend/src/lib/components/layout/BulkActionBar.test.ts
// v0.4.32 — sticky bulk actions bar renders/hides per count.
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import BulkActionBar from './BulkActionBar.svelte';

describe('BulkActionBar', () => {
  it('Test 20: renders count + 3 actions when count > 0; hides when count === 0', () => {
    const props = {
      count: 3,
      onDelete: vi.fn(),
      onExport: vi.fn(),
      onClear: vi.fn(),
      inFlight: false,
    };
    const { getByText, getByRole, rerender, queryByText } = render(BulkActionBar, props);
    expect(getByText(/3 selected/)).toBeTruthy();
    expect(getByRole('button', { name: /Delete 3/ })).toBeTruthy();
    expect(getByRole('button', { name: /Export 3/ })).toBeTruthy();
    expect(getByRole('button', { name: /Clear selection/ })).toBeTruthy();

    rerender({ ...props, count: 0 });
    expect(queryByText(/0 selected/)).toBeNull();
  });
});
