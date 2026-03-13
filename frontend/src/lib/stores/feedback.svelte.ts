/**
 * Feedback + adaptation state store.
 * Manages user feedback submission, dimension overrides, adaptation transparency,
 * pulse status, and adaptation summary loading.
 */

import {
  submitFeedback,
  getFeedback,
  getFeedbackStats,
  getAdaptationPulse,
  getAdaptationSummary,
  type FeedbackConfirmation,
} from '$lib/api/client';

export interface FeedbackState {
  rating: -1 | 0 | 1 | null;
  dimensionOverrides: Record<string, number>;
  correctedIssues: string[];
  comment: string;
  submitting: boolean;
}

export interface AdaptationState {
  dimensionWeights: Record<string, number> | null;
  strategyAffinities: Record<string, any> | null;
  retryThreshold: number;
  feedbackCount: number;
}

export interface AdaptationPulseState {
  status: 'inactive' | 'learning' | 'active';
  label: string;
  detail: string;
}

export interface AdaptationSummaryState {
  feedbackCount: number;
  priorities: Array<{
    dimension: string;
    weight: number;
    shift: number;
    direction: 'up' | 'down';
  }>;
  activeGuardrails: string[];
  frameworkPreferences: Record<string, number>;
  topFrameworks: string[];
  issueResolution: Record<string, number>;
  retryThreshold: number;
  lastUpdated: string | null;
}

class FeedbackStore {
  currentFeedback = $state<FeedbackState>({
    rating: null,
    dimensionOverrides: {},
    correctedIssues: [],
    comment: '',
    submitting: false,
  });

  aggregate = $state<{
    totalRatings: number;
    positive: number;
    negative: number;
    neutral: number;
  }>({ totalRatings: 0, positive: 0, negative: 0, neutral: 0 });

  adaptationState = $state<AdaptationState | null>(null);
  adaptationPulse = $state<AdaptationPulseState | null>(null);
  adaptationSummary = $state<AdaptationSummaryState | null>(null);
  currentOptimizationId = $state<string | null>(null);
  error = $state<string | null>(null);

  async loadFeedback(optimizationId: string) {
    this.currentOptimizationId = optimizationId;
    this.error = null;
    try {
      const result = await getFeedback(optimizationId);
      if (result.feedback) {
        this.currentFeedback.rating = result.feedback.rating;
        this.currentFeedback.dimensionOverrides = result.feedback.dimension_overrides || {};
        this.currentFeedback.correctedIssues = result.feedback.corrected_issues || [];
        this.currentFeedback.comment = result.feedback.comment || '';
      } else {
        this.resetFeedback();
      }
      if (result.aggregate) {
        this.aggregate = {
          totalRatings: result.aggregate.total_ratings,
          positive: result.aggregate.positive,
          negative: result.aggregate.negative,
          neutral: result.aggregate.neutral,
        };
      }
    } catch (err) {
      this.error = err instanceof Error ? err.message : 'Failed to load feedback';
    }
  }

  async submit(optimizationId: string): Promise<FeedbackConfirmation | null> {
    if (this.currentFeedback.rating === null) return null;
    this.currentFeedback.submitting = true;
    this.error = null;

    const body: any = { rating: this.currentFeedback.rating };
    if (Object.keys(this.currentFeedback.dimensionOverrides).length > 0) {
      body.dimension_overrides = this.currentFeedback.dimensionOverrides;
    }
    if (this.currentFeedback.correctedIssues.length > 0) {
      body.corrected_issues = this.currentFeedback.correctedIssues;
    }
    if (this.currentFeedback.comment) {
      body.comment = this.currentFeedback.comment;
    }

    try {
      const confirmation = await submitFeedback(optimizationId, body);
      await this.loadFeedback(optimizationId);
      return confirmation;
    } catch (err) {
      // Auto-retry once after 2s
      await new Promise(resolve => setTimeout(resolve, 2000));
      try {
        const confirmation = await submitFeedback(optimizationId, body);
        await this.loadFeedback(optimizationId);
        return confirmation;
      } catch (retryErr) {
        this.error = retryErr instanceof Error ? retryErr.message : 'Feedback submission failed';
        return null;
      }
    } finally {
      this.currentFeedback.submitting = false;
    }
  }

  setRating(rating: -1 | 0 | 1) {
    this.currentFeedback.rating = rating;
  }

  setDimensionOverride(dimension: string, score: number) {
    this.currentFeedback.dimensionOverrides[dimension] = score;
  }

  removeDimensionOverride(dimension: string) {
    delete this.currentFeedback.dimensionOverrides[dimension];
  }

  setCorrectedIssues(issues: string[]) {
    this.currentFeedback.correctedIssues = issues;
  }

  toggleCorrectedIssue(issueId: string) {
    const idx = this.currentFeedback.correctedIssues.indexOf(issueId);
    if (idx >= 0) {
      this.currentFeedback.correctedIssues = this.currentFeedback.correctedIssues.filter(
        (id) => id !== issueId,
      );
    } else {
      this.currentFeedback.correctedIssues = [...this.currentFeedback.correctedIssues, issueId];
    }
  }

  async loadAdaptationState() {
    this.error = null;
    try {
      const stats = await getFeedbackStats();
      if (stats.adaptation_state) {
        this.adaptationState = {
          dimensionWeights: stats.adaptation_state.dimension_weights,
          strategyAffinities: stats.adaptation_state.strategy_affinities,
          retryThreshold: stats.adaptation_state.retry_threshold,
          feedbackCount: stats.adaptation_state.feedback_count,
        };
      }
    } catch (err) {
      this.error = err instanceof Error ? err.message : 'Failed to load adaptation state';
    }
  }

  async loadAdaptationPulse() {
    try {
      const pulse = await getAdaptationPulse();
      this.adaptationPulse = {
        status: pulse.status,
        label: pulse.label,
        detail: pulse.detail,
      };
    } catch (err) {
      this.error = err instanceof Error ? err.message : 'Failed to load adaptation pulse';
    }
  }

  async loadAdaptationSummary() {
    try {
      const summary = await getAdaptationSummary();
      this.adaptationSummary = {
        feedbackCount: summary.feedback_count ?? 0,
        priorities: summary.priorities ?? [],
        activeGuardrails: summary.active_guardrails ?? [],
        frameworkPreferences: summary.framework_preferences ?? {},
        topFrameworks: summary.top_frameworks ?? [],
        issueResolution: summary.issue_resolution ?? {},
        retryThreshold: summary.retry_threshold ?? 5.0,
        lastUpdated: summary.last_updated ?? null,
      };
    } catch (err) {
      this.error = err instanceof Error ? err.message : 'Failed to load adaptation summary';
    }
  }

  resetFeedback() {
    this.currentFeedback = {
      rating: null,
      dimensionOverrides: {},
      correctedIssues: [],
      comment: '',
      submitting: false,
    };
  }
}

export const feedback = new FeedbackStore();
