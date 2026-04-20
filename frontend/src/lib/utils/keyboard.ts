// Shared keyboard helpers for tablist-role surfaces.
// WAI-ARIA: arrow keys cycle focus/selection between tabs with wrap-around.

export type TablistOrientation = 'horizontal' | 'vertical';

export interface TablistArrowOptions<T> {
  items: readonly T[];
  current: T;
  orientation?: TablistOrientation;
  wrap?: boolean;
}

export function nextTablistValue<T>(
  event: KeyboardEvent,
  options: TablistArrowOptions<T>,
): T | null {
  const { items, current, orientation = 'vertical', wrap = true } = options;
  if (items.length === 0) return null;

  const prevKey = orientation === 'horizontal' ? 'ArrowLeft' : 'ArrowUp';
  const nextKey = orientation === 'horizontal' ? 'ArrowRight' : 'ArrowDown';

  let delta = 0;
  if (event.key === nextKey) delta = 1;
  else if (event.key === prevKey) delta = -1;
  else if (event.key === 'Home') delta = -items.length;
  else if (event.key === 'End') delta = items.length;
  else return null;

  const index = items.indexOf(current);
  if (index < 0) return items[0];

  let target = index + delta;
  if (event.key === 'Home') target = 0;
  else if (event.key === 'End') target = items.length - 1;

  if (wrap) {
    target = ((target % items.length) + items.length) % items.length;
  } else {
    target = Math.max(0, Math.min(items.length - 1, target));
  }

  return items[target];
}

export function handleTablistArrowKeys<T>(
  event: KeyboardEvent,
  options: TablistArrowOptions<T>,
  onChange: (next: T) => void,
): boolean {
  const next = nextTablistValue(event, options);
  if (next === null || next === options.current) return false;
  event.preventDefault();
  onChange(next);
  return true;
}
