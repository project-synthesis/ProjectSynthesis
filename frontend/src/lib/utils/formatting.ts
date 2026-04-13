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

/**
 * Strip the parent domain prefix from a sub-domain label.
 * "backend-async-system-reliability" with parent "backend" → "async-system-reliability"
 * Returns the full label if no parent match or no parent provided.
 */
export function parseSubDomainLabel(label: string, parentLabel?: string): string {
  if (!parentLabel) return label;
  const prefix = parentLabel.toLowerCase() + '-';
  if (label.toLowerCase().startsWith(prefix)) {
    return label.slice(prefix.length);
  }
  return label;
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
