import { describe, it, expect, vi } from 'vitest';
import {
  formatScore,
  formatDelta,
  formatSignedDelta,
  truncateText,
  copyToClipboard,
  isPassthroughResult,
} from './formatting';

describe('formatScore', () => {
  it('formats a number with 1 decimal by default', () => {
    expect(formatScore(7.56)).toBe('7.6');
  });
  it('formats with custom decimals', () => {
    expect(formatScore(7.567, 2)).toBe('7.57');
  });
  it('returns dash for null', () => {
    expect(formatScore(null)).toBe('--');
  });
  it('returns dash for undefined', () => {
    expect(formatScore(undefined)).toBe('--');
  });
  it('handles zero', () => {
    expect(formatScore(0)).toBe('0.0');
  });
  it('handles 10', () => {
    expect(formatScore(10)).toBe('10.0');
  });
});

describe('formatDelta', () => {
  it('formats positive delta with + prefix', () => {
    expect(formatDelta(2.5)).toBe('+2.5');
  });
  it('formats negative delta with - prefix', () => {
    expect(formatDelta(-1.3)).toBe('-1.3');
  });
  it('formats zero delta without + prefix', () => {
    expect(formatDelta(0)).toBe('0.0');
  });
  it('respects custom decimals', () => {
    expect(formatDelta(2.567, 2)).toContain('2.57');
  });
});

describe('formatSignedDelta', () => {
  it('returns em-dash placeholder for null', () => {
    expect(formatSignedDelta(null)).toBe('—');
  });
  it('returns em-dash placeholder for undefined', () => {
    expect(formatSignedDelta(undefined)).toBe('—');
  });
  it('returns em-dash placeholder for non-finite (NaN)', () => {
    expect(formatSignedDelta(NaN)).toBe('—');
  });
  it('formats positive with + prefix and 2-decimal default', () => {
    expect(formatSignedDelta(0.64)).toBe('+0.64');
  });
  it('formats negative with Unicode U+2212 minus and 2-decimal default', () => {
    expect(formatSignedDelta(-0.64)).toBe('−0.64');
    // Sanity — must be the canonical chromatic minus, not ASCII `-`.
    expect(formatSignedDelta(-0.64).charCodeAt(0)).toBe(0x2212);
  });
  it('respects custom decimals', () => {
    expect(formatSignedDelta(0.5, 1)).toBe('+0.5');
    expect(formatSignedDelta(-0.5, 1)).toBe('−0.5');
  });
  it('zero renders as unsigned 0.00 inside the epsilon zone', () => {
    expect(formatSignedDelta(0)).toBe('0.00');
    expect(formatSignedDelta(0.001)).toBe('0.00');
    expect(formatSignedDelta(-0.001)).toBe('0.00');
  });
  it('epsilon boundary respects the override', () => {
    // 0.05 epsilon — values inside the band collapse to unsigned.
    expect(formatSignedDelta(0.04, 2, 0.05)).toBe('0.00');
    expect(formatSignedDelta(0.06, 2, 0.05)).toBe('+0.06');
  });
});

describe('truncateText', () => {
  it('returns short text unchanged', () => {
    expect(truncateText('hello', 80)).toBe('hello');
  });
  it('truncates long text with ellipsis', () => {
    const long = 'a'.repeat(100);
    const result = truncateText(long, 80);
    expect(result.length).toBeLessThanOrEqual(83); // 80 + '...'
    expect(result).toContain('...');
  });
  it('uses default maxLen of 80', () => {
    const exactlyAt = 'a'.repeat(80);
    expect(truncateText(exactlyAt)).toBe(exactlyAt);
  });
});

describe('isPassthroughResult', () => {
  it('returns true for web_passthrough provider', () => {
    expect(isPassthroughResult({ provider: 'web_passthrough' })).toBe(true);
  });
  it('returns true for mcp_passthrough provider', () => {
    expect(isPassthroughResult({ provider: 'mcp_passthrough' })).toBe(true);
  });
  it('returns false for claude-cli provider', () => {
    expect(isPassthroughResult({ provider: 'claude-cli' })).toBe(false);
  });
  it('returns false for null', () => {
    expect(isPassthroughResult(null)).toBe(false);
  });
  it('returns false for undefined', () => {
    expect(isPassthroughResult(undefined)).toBe(false);
  });
  it('returns false for object without provider', () => {
    expect(isPassthroughResult({})).toBe(false);
  });
});

describe('copyToClipboard', () => {
  it('copies text via clipboard API', async () => {
    const result = await copyToClipboard('hello');
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('hello');
    expect(result).toBe(true);
  });
  it('returns false on failure', async () => {
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValueOnce(new Error('fail'));
    // jsdom doesn't implement execCommand — mock it so the fallback path returns false
    const execCommand = vi.fn().mockReturnValue(false);
    document.execCommand = execCommand;
    const result = await copyToClipboard('hello');
    expect(result).toBe(false);
  });
});
