import { describe, it, expect, vi } from 'vitest';
import { nextTablistValue, handleTablistArrowKeys } from './keyboard';

const mkEvent = (key: string): KeyboardEvent => new KeyboardEvent('keydown', { key });

describe('nextTablistValue — vertical (default)', () => {
  const items = ['a', 'b', 'c'] as const;

  it('ArrowDown moves forward', () => {
    expect(nextTablistValue(mkEvent('ArrowDown'), { items, current: 'a' })).toBe('b');
  });

  it('ArrowUp moves backward', () => {
    expect(nextTablistValue(mkEvent('ArrowUp'), { items, current: 'b' })).toBe('a');
  });

  it('ArrowDown wraps from last to first', () => {
    expect(nextTablistValue(mkEvent('ArrowDown'), { items, current: 'c' })).toBe('a');
  });

  it('ArrowUp wraps from first to last', () => {
    expect(nextTablistValue(mkEvent('ArrowUp'), { items, current: 'a' })).toBe('c');
  });

  it('Home jumps to first', () => {
    expect(nextTablistValue(mkEvent('Home'), { items, current: 'c' })).toBe('a');
  });

  it('End jumps to last', () => {
    expect(nextTablistValue(mkEvent('End'), { items, current: 'a' })).toBe('c');
  });

  it('ignores horizontal arrows when orientation is vertical', () => {
    expect(nextTablistValue(mkEvent('ArrowLeft'), { items, current: 'b' })).toBeNull();
    expect(nextTablistValue(mkEvent('ArrowRight'), { items, current: 'b' })).toBeNull();
  });
});

describe('nextTablistValue — horizontal orientation', () => {
  const items = ['a', 'b', 'c'] as const;
  const opts = { items, current: 'b' as const, orientation: 'horizontal' as const };

  it('ArrowRight moves forward', () => {
    expect(nextTablistValue(mkEvent('ArrowRight'), opts)).toBe('c');
  });

  it('ArrowLeft moves backward', () => {
    expect(nextTablistValue(mkEvent('ArrowLeft'), opts)).toBe('a');
  });

  it('ArrowRight wraps at last', () => {
    expect(nextTablistValue(mkEvent('ArrowRight'), { ...opts, current: 'c' })).toBe('a');
  });

  it('ArrowLeft wraps at first', () => {
    expect(nextTablistValue(mkEvent('ArrowLeft'), { ...opts, current: 'a' })).toBe('c');
  });

  it('ignores vertical arrows when orientation is horizontal', () => {
    expect(nextTablistValue(mkEvent('ArrowUp'), opts)).toBeNull();
    expect(nextTablistValue(mkEvent('ArrowDown'), opts)).toBeNull();
  });
});

describe('nextTablistValue — wrap=false', () => {
  const items = ['a', 'b', 'c'] as const;

  it('clamps at last when moving forward past end', () => {
    expect(
      nextTablistValue(mkEvent('ArrowDown'), { items, current: 'c', wrap: false }),
    ).toBe('c');
  });

  it('clamps at first when moving backward past start', () => {
    expect(
      nextTablistValue(mkEvent('ArrowUp'), { items, current: 'a', wrap: false }),
    ).toBe('a');
  });

  it('Home/End still work with wrap=false', () => {
    expect(
      nextTablistValue(mkEvent('Home'), { items, current: 'c', wrap: false }),
    ).toBe('a');
    expect(
      nextTablistValue(mkEvent('End'), { items, current: 'a', wrap: false }),
    ).toBe('c');
  });
});

describe('nextTablistValue — edge cases', () => {
  it('returns null when items is empty', () => {
    expect(
      nextTablistValue(mkEvent('ArrowDown'), { items: [], current: 'anything' }),
    ).toBeNull();
  });

  it('returns first item when current is not in items', () => {
    expect(
      nextTablistValue(mkEvent('ArrowDown'), { items: ['a', 'b'], current: 'missing' }),
    ).toBe('a');
  });

  it('returns null for unhandled keys', () => {
    expect(
      nextTablistValue(mkEvent('Enter'), { items: ['a', 'b'], current: 'a' }),
    ).toBeNull();
    expect(
      nextTablistValue(mkEvent('Space'), { items: ['a', 'b'], current: 'a' }),
    ).toBeNull();
    expect(
      nextTablistValue(mkEvent('Tab'), { items: ['a', 'b'], current: 'a' }),
    ).toBeNull();
  });

  it('works with single-item list', () => {
    expect(
      nextTablistValue(mkEvent('ArrowDown'), { items: ['only'], current: 'only' }),
    ).toBe('only');
  });
});

describe('handleTablistArrowKeys', () => {
  const items = ['a', 'b', 'c'] as const;

  it('calls onChange and preventDefault when value changes', () => {
    const event = mkEvent('ArrowDown');
    const pd = vi.spyOn(event, 'preventDefault');
    const onChange = vi.fn();

    const handled = handleTablistArrowKeys(event, { items, current: 'a' }, onChange);

    expect(handled).toBe(true);
    expect(pd).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith('b');
  });

  it('returns false and does not fire when key is unhandled', () => {
    const event = mkEvent('Enter');
    const pd = vi.spyOn(event, 'preventDefault');
    const onChange = vi.fn();

    const handled = handleTablistArrowKeys(event, { items, current: 'a' }, onChange);

    expect(handled).toBe(false);
    expect(pd).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('returns false when next value equals current (no-op clamp)', () => {
    const event = mkEvent('ArrowDown');
    const pd = vi.spyOn(event, 'preventDefault');
    const onChange = vi.fn();

    const handled = handleTablistArrowKeys(
      event,
      { items, current: 'c', wrap: false },
      onChange,
    );

    expect(handled).toBe(false);
    expect(pd).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('returns false and does not fire on empty items', () => {
    const event = mkEvent('ArrowDown');
    const onChange = vi.fn();

    const handled = handleTablistArrowKeys(event, { items: [], current: 'x' }, onChange);

    expect(handled).toBe(false);
    expect(onChange).not.toHaveBeenCalled();
  });
});
