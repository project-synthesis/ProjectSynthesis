import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import ScoreCard from './ScoreCard.svelte';
import { mockDimensionScores } from '$lib/test-utils';

describe('ScoreCard', () => {
  afterEach(() => {
    cleanup();
  });

  const defaultScores = mockDimensionScores();

  it('renders all 5 dimension labels', () => {
    render(ScoreCard, { props: { scores: defaultScores } });
    expect(screen.getByText('Clarity')).toBeInTheDocument();
    expect(screen.getByText('Specificity')).toBeInTheDocument();
    expect(screen.getByText('Structure')).toBeInTheDocument();
    expect(screen.getByText('Faithfulness')).toBeInTheDocument();
    expect(screen.getByText('Conciseness')).toBeInTheDocument();
  });

  it('renders score values for each dimension', () => {
    render(ScoreCard, { props: { scores: defaultScores } });
    // defaultScores: clarity: 7.5, specificity: 8.0, structure: 7.0, faithfulness: 9.0, conciseness: 6.5
    expect(screen.getByText('7.5')).toBeInTheDocument();
    expect(screen.getByText('8.0')).toBeInTheDocument();
    expect(screen.getByText('7.0')).toBeInTheDocument();
    expect(screen.getByText('9.0')).toBeInTheDocument();
    expect(screen.getByText('6.5')).toBeInTheDocument();
  });

  it('renders overall score when provided', () => {
    render(ScoreCard, { props: { scores: defaultScores, overallScore: 7.6 } });
    expect(screen.getByText('Overall')).toBeInTheDocument();
    expect(screen.getByText('7.6')).toBeInTheDocument();
  });

  it('does not render overall row when overallScore is null', () => {
    render(ScoreCard, { props: { scores: defaultScores, overallScore: null } });
    expect(screen.queryByText('Overall')).not.toBeInTheDocument();
  });

  it('does not render overall row when overallScore is not provided', () => {
    render(ScoreCard, { props: { scores: defaultScores } });
    expect(screen.queryByText('Overall')).not.toBeInTheDocument();
  });

  it('renders positive deltas when provided', () => {
    const deltas = { clarity: 2.5, specificity: 3.5, structure: 0.0, faithfulness: 0.0, conciseness: 0.0 };
    render(ScoreCard, { props: { scores: defaultScores, deltas } });
    expect(screen.getByText('+2.5')).toBeInTheDocument();
    expect(screen.getByText('+3.5')).toBeInTheDocument();
  });

  it('renders negative deltas when provided', () => {
    const deltas = { clarity: -1.0, specificity: 0.0, structure: 0.0, faithfulness: 0.0, conciseness: -0.5 };
    render(ScoreCard, { props: { scores: defaultScores, deltas } });
    expect(screen.getByText('-1.0')).toBeInTheDocument();
    expect(screen.getByText('-0.5')).toBeInTheDocument();
  });

  it('does not render deltas when not provided', () => {
    render(ScoreCard, { props: { scores: defaultScores } });
    // No + or - prefixed values should appear
    expect(screen.queryByText('+2.5')).not.toBeInTheDocument();
  });

  it('renders the accessible dimension list', () => {
    render(ScoreCard, { props: { scores: defaultScores } });
    const list = screen.getByRole('list', { name: 'Dimension scores' });
    expect(list).toBeInTheDocument();
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(5);
  });

  it('renders original scores when provided', () => {
    const originalScores = mockDimensionScores({ clarity: 5.0, specificity: 4.5 });
    render(ScoreCard, { props: { scores: defaultScores, originalScores } });
    // The original clarity value 5.0 should appear in addition to the current 7.5
    expect(screen.getByText('5.0')).toBeInTheDocument();
    expect(screen.getByText('4.5')).toBeInTheDocument();
  });
});
