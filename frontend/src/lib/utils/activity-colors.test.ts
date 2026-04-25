import { describe, it, expect } from 'vitest';
import { pathColor } from './activity-colors';

describe('pathColor', () => {
  it('returns neon-red token for hot', () => {
    expect(pathColor('hot')).toContain('neon-red');
  });
  it('returns neon-yellow token for warm', () => {
    expect(pathColor('warm')).toContain('neon-yellow');
  });
  it('returns neon-cyan token for cold', () => {
    expect(pathColor('cold')).toContain('neon-cyan');
  });
});
