import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import MarkdownRenderer from './MarkdownRenderer.svelte';

describe('MarkdownRenderer', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders a heading', () => {
    render(MarkdownRenderer, { props: { content: '# Hello World' } });
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toBeInTheDocument();
    expect(heading).toHaveTextContent('Hello World');
  });

  it('renders h2 headings', () => {
    render(MarkdownRenderer, { props: { content: '## Section Title' } });
    const heading = screen.getByRole('heading', { level: 2 });
    expect(heading).toHaveTextContent('Section Title');
  });

  it('renders h3 headings', () => {
    render(MarkdownRenderer, { props: { content: '### Sub-section' } });
    const heading = screen.getByRole('heading', { level: 3 });
    expect(heading).toHaveTextContent('Sub-section');
  });

  it('renders inline code', () => {
    render(MarkdownRenderer, { props: { content: 'Use `console.log()` here.' } });
    const code = document.querySelector('code');
    expect(code).toBeInTheDocument();
    expect(code).toHaveTextContent('console.log()');
  });

  it('renders a fenced code block', () => {
    const content = '```\nconst x = 1;\n```';
    render(MarkdownRenderer, { props: { content } });
    const pre = document.querySelector('pre');
    expect(pre).toBeInTheDocument();
    expect(pre).toHaveTextContent('const x = 1;');
  });

  it('renders an unordered list', () => {
    const content = '- Item one\n- Item two\n- Item three';
    render(MarkdownRenderer, { props: { content } });
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(screen.getByText('Item one')).toBeInTheDocument();
    expect(screen.getByText('Item two')).toBeInTheDocument();
    expect(screen.getByText('Item three')).toBeInTheDocument();
  });

  it('renders an ordered list', () => {
    const content = '1. First\n2. Second\n3. Third';
    render(MarkdownRenderer, { props: { content } });
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
  });

  it('renders paragraph text', () => {
    render(MarkdownRenderer, { props: { content: 'This is a paragraph.' } });
    expect(screen.getByText('This is a paragraph.')).toBeInTheDocument();
  });

  it('renders empty content as empty container', () => {
    const { container } = render(MarkdownRenderer, { props: { content: '' } });
    const div = container.querySelector('.md-render');
    expect(div).toBeInTheDocument();
    expect(div!.innerHTML).toBe('');
  });

  it('renders bold text', () => {
    render(MarkdownRenderer, { props: { content: '**bold text**' } });
    const strong = document.querySelector('strong');
    expect(strong).toBeInTheDocument();
    expect(strong).toHaveTextContent('bold text');
  });

  it('renders italic text', () => {
    render(MarkdownRenderer, { props: { content: '*italic text*' } });
    const em = document.querySelector('em');
    expect(em).toBeInTheDocument();
    expect(em).toHaveTextContent('italic text');
  });

  it('wraps content in md-render div', () => {
    const { container } = render(MarkdownRenderer, { props: { content: '# Test' } });
    expect(container.querySelector('.md-render')).toBeInTheDocument();
  });

  it('passes additional class to wrapper div', () => {
    const { container } = render(MarkdownRenderer, { props: { content: 'hello', class: 'extra-class' } });
    expect(container.querySelector('.md-render.extra-class')).toBeInTheDocument();
  });
});
