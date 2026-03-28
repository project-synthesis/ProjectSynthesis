/**
 * Shared types for the tier guide modal system (TierGuide, PassthroughGuide, SamplingGuide).
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

export interface GuideStep {
  number: number;
  title: string;
  description: string;
  detail: string;
  accent: 'yellow' | 'cyan' | 'green';
}

export interface ComparisonRow {
  feature: string;
  internal: string;
  sampling: string;
  passthrough: string;
}

export type HighlightColumn = 'internal' | 'sampling' | 'passthrough';

/** Shared feature comparison matrix — identical across all tier guides. */
export const TIER_COMPARISON: ComparisonRow[] = [
  { feature: 'Analyze phase', internal: '\u2713', sampling: '\u2713', passthrough: 'Implicit' },
  { feature: 'Optimize phase', internal: '\u2713', sampling: '\u2713', passthrough: 'Single-shot' },
  { feature: 'Score phase', internal: 'LLM', sampling: 'LLM', passthrough: 'Heuristic / Hybrid' },
  { feature: 'Codebase explore', internal: '\u2713', sampling: '\u2713', passthrough: 'Roots + index' },
  { feature: 'Pattern injection', internal: '\u2713', sampling: '\u2713', passthrough: '\u2713' },
  { feature: 'Suggestions', internal: '\u2713', sampling: '\u2713', passthrough: '\u2713' },
  { feature: 'Intent drift', internal: '\u2713', sampling: '\u2713', passthrough: '\u2717' },
  { feature: 'Adaptation state', internal: '\u2713', sampling: '\u2713', passthrough: 'Injected' },
  { feature: 'Strategy template', internal: '\u2713', sampling: '\u2713', passthrough: 'Injected' },
  { feature: 'Cost', internal: 'API key', sampling: 'IDE LLM', passthrough: 'Zero' },
  { feature: 'Dependencies', internal: 'Provider', sampling: 'MCP client', passthrough: 'None' },
];
