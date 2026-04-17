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
    // Svelte 5 renders empty {@html ''} as an HTML comment node
    expect(div!.textContent).toBe('');
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

  // ---- Prompt-structuring pseudo-XML wrappers -----------------------
  // Optimizer outputs often wrap sections in <context>, <requirements>,
  // <constraints>, <instructions>, <deliverables>, <verification>, etc.
  // CommonMark treats an unknown opening tag on its own line as an HTML
  // block (type 7), suppressing markdown on the immediately following
  // line until the next blank line.  The line right after the wrapper
  // thus renders as literal `**text**` instead of <strong>text</strong>.

  it('renders bold-with-colon on the line right after <context>', () => {
    const content = '<context>\n**Singleton architecture**: detail text.';
    const { container } = render(MarkdownRenderer, { props: { content } });
    const strong = container.querySelector('strong');
    expect(strong).toBeInTheDocument();
    expect(strong).toHaveTextContent('Singleton architecture');
  });

  it('renders bullet list on the line right after <requirements>', () => {
    const content = '<requirements>\n- First requirement\n- Second requirement';
    const { container } = render(MarkdownRenderer, { props: { content } });
    const items = container.querySelectorAll('li');
    expect(items.length).toBe(2);
    expect(items[0]).toHaveTextContent('First requirement');
  });

  it('renders headings on the line right after <instructions>', () => {
    const content = '<instructions>\n## Phase One';
    const { container } = render(MarkdownRenderer, { props: { content } });
    const h2 = container.querySelector('h2');
    expect(h2).toBeInTheDocument();
    expect(h2).toHaveTextContent('Phase One');
  });

  it('preserves inline placeholder tags like <seconds> in paragraph text', () => {
    const content = 'Retry after <seconds> seconds have elapsed.';
    const { container } = render(MarkdownRenderer, { props: { content } });
    expect(container.textContent).toContain('<seconds>');
  });

  it('does not leak the literal wrapper tag as visible text', () => {
    const content = '<context>\n**Bold**: value\n</context>';
    const { container } = render(MarkdownRenderer, { props: { content } });
    expect(container.textContent).not.toContain('<context>');
    expect(container.textContent).not.toContain('</context>');
  });
});
