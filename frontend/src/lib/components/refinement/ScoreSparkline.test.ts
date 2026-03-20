import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import ScoreSparkline from './ScoreSparkline.svelte';

describe('ScoreSparkline', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders an SVG element when at least 2 scores are provided', () => {
    render(ScoreSparkline, { props: { scores: [7.0, 8.5] } });
    expect(screen.getByRole('img', { name: 'Score progression sparkline' })).toBeInTheDocument();
  });

  it('renders a polyline inside the SVG', () => {
    const { container } = render(ScoreSparkline, { props: { scores: [7.0, 8.5, 9.0] } });
    const polyline = container.querySelector('polyline');
    expect(polyline).toBeInTheDocument();
  });

  it('polyline has a non-empty points attribute', () => {
    const { container } = render(ScoreSparkline, { props: { scores: [7.0, 8.5, 9.0] } });
    const polyline = container.querySelector('polyline');
    expect(polyline).toHaveAttribute('points');
    expect(polyline!.getAttribute('points')).not.toBe('');
  });

  it('does not render SVG when scores array is empty', () => {
    render(ScoreSparkline, { props: { scores: [] } });
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('does not render SVG when only a single score is provided', () => {
    render(ScoreSparkline, { props: { scores: [7.0] } });
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('renders SVG with correct dimensions', () => {
    const { container } = render(ScoreSparkline, { props: { scores: [6.0, 8.0] } });
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('width', '120');
    expect(svg).toHaveAttribute('height', '24');
  });

  it('renders with more than 2 scores', () => {
    render(ScoreSparkline, { props: { scores: [5.0, 6.5, 7.0, 8.0, 9.5] } });
    expect(screen.getByRole('img', { name: 'Score progression sparkline' })).toBeInTheDocument();
  });

  it('renders when all scores are identical (flat line)', () => {
    const { container } = render(ScoreSparkline, { props: { scores: [7.5, 7.5, 7.5] } });
    // Should still render the SVG
    expect(screen.getByRole('img', { name: 'Score progression sparkline' })).toBeInTheDocument();
    const polyline = container.querySelector('polyline');
    expect(polyline).toBeInTheDocument();
  });

  it('has fill="none" on the polyline', () => {
    const { container } = render(ScoreSparkline, { props: { scores: [7.0, 8.0] } });
    const polyline = container.querySelector('polyline');
    expect(polyline).toHaveAttribute('fill', 'none');
  });
});
