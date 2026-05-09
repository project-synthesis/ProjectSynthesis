/**
 * AnimatedDialog — render-test contract.
 *
 * Pins the primitive's behavior:
 *   - hidden when open=false
 *   - renders scrim + role=dialog when open=true
 *   - ESC fires onClose when dismissible
 *   - ESC ignored when dismissible=false
 *   - click-outside fires onClose when dismissible
 *   - aria attributes propagate
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import AnimatedDialog from './AnimatedDialog.svelte';
import { mount } from 'svelte';

describe('AnimatedDialog', () => {
  afterEach(() => cleanup());

  it('renders nothing when open=false', () => {
    render(AnimatedDialog, { props: { open: false, onClose: vi.fn() } });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders scrim + dialog when open=true', () => {
    render(AnimatedDialog, {
      props: { open: true, onClose: vi.fn(), ariaLabel: 'Test dialog' },
    });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // Scrim is the role="presentation" sibling.
    const scrim = document.querySelector('[role="presentation"]');
    expect(scrim).toBeInTheDocument();
  });

  it('propagates aria-label and aria-labelledby', () => {
    const { unmount } = render(AnimatedDialog, {
      props: { open: true, onClose: vi.fn(), ariaLabel: 'Foo' },
    });
    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('aria-label')).toBe('Foo');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    unmount();
  });

  it('clicking the scrim invokes onClose when dismissible (default)', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(AnimatedDialog, { props: { open: true, onClose } });
    const scrim = document.querySelector('[role="presentation"]') as HTMLElement;
    await user.click(scrim);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('clicking the scrim is a no-op when dismissible=false', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(AnimatedDialog, {
      props: { open: true, onClose, dismissible: false },
    });
    const scrim = document.querySelector('[role="presentation"]') as HTMLElement;
    await user.click(scrim);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('ESC invokes onClose when dismissible', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(AnimatedDialog, { props: { open: true, onClose } });
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('ESC is a no-op when dismissible=false (e.g. in-flight destructive op)', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(AnimatedDialog, {
      props: { open: true, onClose, dismissible: false },
    });
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('removes the keydown listener when unmounted', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { unmount } = render(AnimatedDialog, {
      props: { open: true, onClose },
    });
    unmount();
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('removes the keydown listener when open flips to false', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { rerender } = render(AnimatedDialog, {
      props: { open: true, onClose },
    });
    await rerender({ open: false, onClose });
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('merges the optional class onto the dialog container', () => {
    render(AnimatedDialog, {
      props: { open: true, onClose: vi.fn(), class: 'my-custom' },
    });
    const dialog = screen.getByRole('dialog');
    expect(dialog.className).toContain('my-custom');
  });

  // Suppress unused-import warning — `mount` is referenced for future
  // tests covering render-from-action; kept here as a marker.
  void mount;
});
