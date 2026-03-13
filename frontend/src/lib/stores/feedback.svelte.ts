/**
 * Feedback + adaptation state store.
 * Manages user feedback submission, dimension overrides, and adaptation transparency.
 */

import { submitFeedback, getFeedback, getFeedbackStats } from '$lib/api/client';

export interface FeedbackState {
  rating: -1 | 0 | 1 | null;
  dimensionOverrides: Record<string, number>;
  comment: string;
  submitting: boolean;
}

export interface AdaptationState {
  dimensionWeights: Record<string, number> | null;
  strategyAffinities: Record<string, any> | null;
  retryThreshold: number;
  feedbackCount: number;
}

class FeedbackStore {
  currentFeedback = $state<FeedbackState>({
    rating: null,
    dimensionOverrides: {},
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
  currentOptimizationId = $state<string | null>(null);

  async loadFeedback(optimizationId: string) {
    this.currentOptimizationId = optimizationId;
    try {
      const result = await getFeedback(optimizationId);
      if (result.feedback) {
        this.currentFeedback.rating = result.feedback.rating;
        this.currentFeedback.dimensionOverrides = result.feedback.dimension_overrides || {};
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
    } catch {
      // Silent fail — feedback is non-critical
    }
  }

  async submit(optimizationId: string) {
    if (this.currentFeedback.rating === null) return;
    this.currentFeedback.submitting = true;
    try {
      const body: any = { rating: this.currentFeedback.rating };
      if (Object.keys(this.currentFeedback.dimensionOverrides).length > 0) {
        body.dimension_overrides = this.currentFeedback.dimensionOverrides;
      }
      if (this.currentFeedback.comment) {
        body.comment = this.currentFeedback.comment;
      }
      await submitFeedback(optimizationId, body);
      await this.loadFeedback(optimizationId);
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

  async loadAdaptationState() {
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
    } catch {
      // Silent fail
    }
  }

  resetFeedback() {
    this.currentFeedback = {
      rating: null,
      dimensionOverrides: {},
      comment: '',
      submitting: false,
    };
  }
}

export const feedback = new FeedbackStore();
