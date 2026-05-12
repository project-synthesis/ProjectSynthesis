/**
 * Shared formatting utilities for scores, numbers, and text display.
 */

/** Format a numeric score for display (e.g. 7.3). Returns '--' for null/undefined/NaN/Infinity. */
export function formatScore(score: number | null | undefined, decimals = 1): string {
  if (score == null || !isFinite(score)) return '--';
  return score.toFixed(decimals);
}

/** Format a delta value with sign prefix (e.g. '+1.2', '-0.3'). */
export function formatDelta(delta: number, decimals = 1): string {
  return (delta > 0 ? '+' : '') + delta.toFixed(decimals);
}

/**
 * Format a signed delta with Unicode U+2212 minus (canonical "chromatic
 * minus" per SKILL.md numeric voice). Null-safe — returns the placeholder
 * `—` (U+2014 em-dash) for null/undefined. Below the epsilon zone (default
 * `0.005`) the delta is rendered as `0.NN` without a sign, so display
 * doesn't show `+0.00` / `−0.00` for jitter-class values.
 *
 * Used by the Topic Probe Tier 2 suite surfaces (SuiteRow + SuiteDetailView)
 * where signed deltas vs baseline are surfaced inline; the scoring
 * convention shows two decimals (`−0.64 vs baseline` per spec § 6 voice
 * table).
 */
export function formatSignedDelta(
  delta: number | null | undefined,
  decimals = 2,
  epsilon = 0.005,
): string {
  if (delta == null || !isFinite(delta)) return '—';
  const abs = Math.abs(delta).toFixed(decimals);
  if (delta > epsilon) return `+${abs}`;
  if (delta < -epsilon) return `−${abs}`;
  return (0).toFixed(decimals);
}

/** Truncate text to maxLen characters, appending '...' if truncated. Returns '' for null/undefined. */
export function truncateText(text: string | null | undefined, maxLen = 80): string {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).trimEnd() + '...';
}

/** Compact relative time string for sidebar display (e.g. "2h", "3d", "1mo"). */
export function formatRelativeTime(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime();
  if (diff < 0) return 'now';
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return 'now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  if (days < 30) return `${Math.floor(days / 7)}w`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
}

/** Trend threshold (absolute) below which the trend is considered stable. */
const TREND_THRESHOLD = 0.1;

/** Classify a trend value into label, color, and char for consistent display. */
export function trendInfo(trend: number): { label: string; color: string; char: string } {
  if (trend > TREND_THRESHOLD) {
    return { label: 'improving', color: 'var(--color-neon-green)', char: '/' };
  }
  if (trend < -TREND_THRESHOLD) {
    return { label: 'declining', color: 'var(--color-neon-red)', char: '\\' };
  }
  return { label: 'stable', color: 'var(--color-text-dim)', char: '-' };
}

/** Extract primary domain from "primary: qualifier" format, lowercased. */
export function parsePrimaryDomain(domain: string | null | undefined): string {
  if (!domain) return 'general';
  const idx = domain.indexOf(':');
  return (idx >= 0 ? domain.substring(0, idx).trim() : domain.trim()).toLowerCase() || 'general';
}

/** Format a character count as compact "K" string (e.g. 27286 -> "27.3K"). */
export function formatCompactChars(chars: number): string {
  if (chars < 1000) return String(chars);
  return (chars / 1000).toFixed(1) + 'K';
}

/** True when the result was produced via a passthrough flow (web or MCP). */
export function isPassthroughResult(result: { provider?: string } | null | undefined): boolean {
  return result?.provider?.endsWith('_passthrough') === true;
}

/**
 * Copy text to clipboard with fallback for older browsers.
 * Returns true on success, false on failure.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback: temporary textarea + execCommand
    const el = document.createElement('textarea');
    el.value = text;
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(el);
    return ok;
  }
}
