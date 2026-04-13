import { describe, it, expect, vi } from 'vitest';
import { formatScore, formatDelta, truncateText, copyToClipboard, isPassthroughResult, parseSubDomainLabel } from './formatting';

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

describe('parseSubDomainLabel', () => {
  it('strips parent prefix', () => {
    expect(parseSubDomainLabel('backend-async-system-reliability', 'backend'))
      .toBe('async-system-reliability');
  });

  it('returns full label without parent', () => {
    expect(parseSubDomainLabel('backend-auth')).toBe('backend-auth');
  });

  it('returns full label when no prefix match', () => {
    expect(parseSubDomainLabel('frontend-auth', 'backend')).toBe('frontend-auth');
  });

  it('is case-insensitive', () => {
    expect(parseSubDomainLabel('Backend-Auth', 'backend')).toBe('Auth');
  });

  it('handles top-level domain unchanged', () => {
    expect(parseSubDomainLabel('backend', 'backend')).toBe('backend');
  });
});
