import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import DrillButton from './DrillButton.svelte';

const sampleCluster = {
  id: 'c-abc',
  label: 'react testing',
  domain: 'frontend',
  task_type: 'coding',
};

describe('DrillButton', () => {
  it('renders once per cluster with aria-label referencing the cluster label', () => {
    const { getByRole } = render(DrillButton, { cluster: sampleCluster });
    const btn = getByRole('button', { name: /drill into cluster react testing/i });
    expect(btn).toBeTruthy();
  });

  it('opens the modal on click', async () => {
    const { getByRole, queryByRole } = render(DrillButton, { cluster: sampleCluster });
    expect(queryByRole('dialog')).toBeNull();
    await fireEvent.click(getByRole('button'));
    expect(queryByRole('dialog')).toBeTruthy();
  });
});
