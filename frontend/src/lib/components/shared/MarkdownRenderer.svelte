<script lang="ts">
  import { marked } from 'marked';

  interface Props {
    content: string;
    class?: string;
  }

  let { content, class: className = '' }: Props = $props();

  // Real HTML5 element whitelist. Tags outside this set are treated as
  // pseudo-XML artifacts from prompt-structuring LLMs (e.g. <context>,
  // <requirements>, <instructions>) and either stripped (when block-
  // level, i.e. alone on a line) or escaped (when inline). This prevents
  // two CommonMark failure modes:
  //   1. An unknown opening tag on its own line starts an HTML block
  //      (type 7), which suppresses markdown parsing on the very next
  //      line until the following blank line — the reason a wrapped
  //      **Bold**: renders as literal ** instead of <strong>.
  //   2. An unknown inline tag like <seconds> becomes an empty element
  //      with no visible text, erasing prompt placeholders from view.
  const HTML_TAG_WHITELIST: ReadonlySet<string> = new Set([
    'a','abbr','address','area','article','aside','audio',
    'b','base','bdi','bdo','blockquote','body','br','button',
    'canvas','caption','cite','code','col','colgroup',
    'data','datalist','dd','del','details','dfn','dialog','div','dl','dt',
    'em','embed','fieldset','figcaption','figure','footer','form',
    'h1','h2','h3','h4','h5','h6','head','header','hgroup','hr','html',
    'i','iframe','img','input','ins',
    'kbd','label','legend','li','link',
    'main','map','mark','menu','meta','meter',
    'nav','noscript',
    'object','ol','optgroup','option','output',
    'p','param','picture','pre','progress',
    'q','rp','rt','ruby',
    's','samp','script','section','select','slot','small','source','span','strong','style','sub','summary','sup','svg',
    'table','tbody','td','template','textarea','tfoot','th','thead','time','title','tr','track',
    'u','ul','var','video','wbr',
  ]);

  function sanitizePseudoXml(raw: string): string {
    // 1. Strip block-level pseudo-XML wrappers (tag alone on its line).
    //    Consumes the trailing newline so markdown structure below is
    //    unaffected (lists, headings, bold-colon lines all parse cleanly).
    const blockRe = /^[ \t]*<(\/?)([a-zA-Z][a-zA-Z0-9_-]*)(?:\s[^<>]*)?>[ \t]*\r?\n?/gm;
    let out = raw.replace(blockRe, (match, _slash, name) =>
      HTML_TAG_WHITELIST.has(name.toLowerCase()) ? match : ''
    );
    // 2. Escape remaining inline pseudo-XML tags so they render as
    //    literal text (e.g. placeholders like <seconds>) instead of
    //    empty unknown elements.
    const inlineRe = /<(\/?)([a-zA-Z][a-zA-Z0-9_-]*)((?:\s[^<>]*)?)>/g;
    out = out.replace(inlineRe, (match, slash, name, attrs) =>
      HTML_TAG_WHITELIST.has(name.toLowerCase())
        ? match
        : `&lt;${slash}${name}${attrs}&gt;`
    );
    return out;
  }

  const rendered = $derived.by(() => {
    if (!content) return '';
    const safe = sanitizePseudoXml(content);
    return marked.parse(safe, { breaks: true, gfm: true, async: false }) as string;
  });
</script>

<div class="md-render {className}">
  {@html rendered}
</div>

<style>
  /* ================================================================
     Brand-Compliant Markdown Rendering
     Industrial cyberpunk aesthetic — dark backgrounds, 1px neon
     contours, monospace data, ultra-compact density.
     ================================================================ */

  .md-render {
    font-family: var(--font-sans);
    font-size: 12px;
    line-height: 1.6;
    color: var(--color-text-primary);
    word-break: break-word;
    overflow-wrap: anywhere;
    min-width: 0;
    max-width: 100%;
  }

  /* ---- Headings ---- */
  .md-render :global(h1),
  .md-render :global(h2),
  .md-render :global(h3),
  .md-render :global(h4),
  .md-render :global(h5),
  .md-render :global(h6) {
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-text-primary);
    margin: 0;
    padding: 0;
    line-height: 1.3;
  }

  .md-render :global(h1) {
    font-size: 14px;
    color: var(--tier-accent, var(--color-neon-cyan));
    border-bottom: 1px solid var(--color-border-subtle);
    padding-bottom: 4px;
    margin-bottom: 8px;
    margin-top: 12px;
  }

  .md-render :global(h2) {
    font-size: 12px;
    color: var(--tier-accent, var(--color-neon-cyan));
    border-bottom: 1px solid var(--color-border-subtle);
    padding-bottom: 3px;
    margin-bottom: 6px;
    margin-top: 10px;
  }

  .md-render :global(h3) {
    font-size: 11px;
    color: var(--color-text-primary);
    margin-bottom: 4px;
    margin-top: 8px;
  }

  .md-render :global(h4) {
    font-size: 10px;
    color: var(--color-text-secondary);
    margin-bottom: 3px;
    margin-top: 6px;
  }

  .md-render :global(h5),
  .md-render :global(h6) {
    font-size: 10px;
    color: var(--color-text-dim);
    margin-bottom: 2px;
    margin-top: 6px;
  }

  /* First heading — no top margin */
  .md-render :global(:first-child) {
    margin-top: 0;
  }

  /* ---- Paragraphs ---- */
  .md-render :global(p) {
    margin: 0 0 6px 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--color-text-primary);
  }

  .md-render :global(p:last-child) {
    margin-bottom: 0;
  }

  /* ---- Strong / Bold ---- */
  .md-render :global(strong) {
    font-weight: 600;
    color: var(--color-text-primary);
  }

  /* ---- Emphasis / Italic ---- */
  .md-render :global(em) {
    font-style: italic;
    color: var(--color-text-secondary);
  }

  /* ---- Inline Code ---- */
  .md-render :global(code) {
    font-family: var(--font-mono);
    font-size: 11px;
    background: var(--color-bg-hover);
    border: 1px solid var(--color-border-subtle);
    color: var(--tier-accent, var(--color-neon-cyan));
    padding: 1px 4px;
    line-height: 1.4;
  }

  /* ---- Code Blocks (fenced) ---- */
  .md-render :global(pre) {
    margin: 6px 0;
    padding: 0;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    overflow-x: auto;
  }

  .md-render :global(pre code) {
    display: block;
    padding: 6px 8px;
    background: transparent;
    border: none;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.5;
    white-space: pre;
    overflow-x: auto;
  }

  /* ---- Blockquotes ---- */
  .md-render :global(blockquote) {
    margin: 6px 0;
    padding: 4px 8px;
    border-left: 1px solid var(--color-neon-purple);
    background: color-mix(in srgb, var(--color-neon-purple) 4%, transparent);
    color: var(--color-text-secondary);
    font-style: italic;
  }

  .md-render :global(blockquote p) {
    margin: 0;
    color: var(--color-text-secondary);
  }

  .md-render :global(blockquote blockquote) {
    margin-top: 4px;
    border-left-color: var(--color-neon-indigo);
    background: color-mix(in srgb, var(--color-neon-indigo) 4%, transparent);
  }

  /* ---- Unordered Lists ---- */
  .md-render :global(ul) {
    margin: 4px 0;
    padding-left: 16px;
    list-style: none;
  }

  .md-render :global(ul li) {
    position: relative;
    margin-bottom: 2px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-text-primary);
    padding-left: 4px;
  }

  .md-render :global(ul li::before) {
    content: '▸';
    position: absolute;
    left: -14px;
    color: var(--tier-accent, var(--color-neon-cyan));
    font-size: 10px;
    line-height: 1.8;
  }

  /* Nested list markers — chromatic cascade */
  .md-render :global(ul ul li::before) {
    content: '▹';
    color: var(--color-neon-purple);
  }

  .md-render :global(ul ul ul li::before) {
    content: '·';
    color: var(--color-neon-teal);
  }

  /* ---- Ordered Lists ---- */
  .md-render :global(ol) {
    margin: 4px 0;
    padding-left: 20px;
    list-style: none;
    counter-reset: md-ol;
  }

  .md-render :global(ol li) {
    position: relative;
    margin-bottom: 2px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-text-primary);
    counter-increment: md-ol;
    padding-left: 2px;
  }

  .md-render :global(ol li::before) {
    content: counter(md-ol) '.';
    position: absolute;
    left: -20px;
    width: 16px;
    text-align: right;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--tier-accent, var(--color-neon-cyan));
    line-height: 1.8;
  }

  /* ---- Task Lists (checkboxes) ---- */
  .md-render :global(ul li input[type="checkbox"]) {
    appearance: none;
    width: 10px;
    height: 10px;
    border: 1px solid var(--color-border-subtle);
    background: var(--color-bg-input);
    vertical-align: middle;
    margin-right: 4px;
    position: relative;
    top: -1px;
    cursor: default;
  }

  .md-render :global(ul li input[type="checkbox"]:checked) {
    border-color: var(--color-neon-green);
    background: color-mix(in srgb, var(--color-neon-green) 12%, transparent);
  }

  .md-render :global(ul li input[type="checkbox"]:checked::after) {
    content: '✓';
    position: absolute;
    top: -2px;
    left: 1px;
    font-size: 8px;
    color: var(--color-neon-green);
  }

  /* Task list items — no bullet marker */
  .md-render :global(.task-list-item::before) {
    content: none !important;
  }

  /* ---- Horizontal Rule ---- */
  .md-render :global(hr) {
    border: none;
    height: 1px;
    background: var(--color-border-subtle);
    margin: 8px 0;
  }

  /* ---- Tables ---- */
  .md-render :global(table) {
    width: 100%;
    border-collapse: collapse;
    margin: 6px 0;
    font-size: 11px;
    border: 1px solid var(--color-border-subtle);
  }

  .md-render :global(thead) {
    background: var(--color-bg-secondary);
  }

  .md-render :global(th) {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-dim);
    text-align: left;
    padding: 3px 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    border-right: 1px solid var(--color-border-subtle);
  }

  .md-render :global(th:last-child) {
    border-right: none;
  }

  .md-render :global(td) {
    padding: 3px 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    border-right: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-size: 11px;
    line-height: 1.4;
  }

  .md-render :global(td:last-child) {
    border-right: none;
  }

  .md-render :global(tr:last-child td) {
    border-bottom: none;
  }

  .md-render :global(tr:hover td) {
    background: var(--color-bg-hover);
  }

  /* ---- Links ---- */
  .md-render :global(a) {
    color: var(--tier-accent, var(--color-neon-cyan));
    text-decoration: none;
    border-bottom: 1px solid rgba(var(--tier-accent-rgb, 0, 229, 255), 0.2);
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .md-render :global(a:hover) {
    border-bottom-color: var(--tier-accent, var(--color-neon-cyan));
  }

  /* ---- Images ---- */
  .md-render :global(img) {
    max-width: 100%;
    height: auto;
    border: 1px solid var(--color-border-subtle);
    margin: 4px 0;
  }

  /* ---- Strikethrough ---- */
  .md-render :global(del) {
    color: var(--color-text-dim);
    text-decoration: line-through;
    text-decoration-color: var(--color-neon-red);
  }

  /* ---- Mark / Highlight ---- */
  .md-render :global(mark) {
    background: color-mix(in srgb, var(--color-neon-yellow) 15%, transparent);
    color: var(--color-neon-yellow);
    padding: 0 2px;
  }

  /* ---- Definition-like patterns (bold followed by text) ---- */
  .md-render :global(dl) {
    margin: 4px 0;
  }

  .md-render :global(dt) {
    font-weight: 600;
    color: var(--color-text-primary);
    font-size: 11px;
    margin-top: 4px;
  }

  .md-render :global(dd) {
    margin-left: 12px;
    color: var(--color-text-secondary);
    font-size: 11px;
  }

  /* ---- Abbreviations ---- */
  .md-render :global(abbr) {
    border-bottom: 1px dotted var(--color-text-dim);
    cursor: help;
  }

  /* ---- Keyboard shortcuts ---- */
  .md-render :global(kbd) {
    font-family: var(--font-mono);
    font-size: 10px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
    padding: 1px 4px;
    color: var(--color-text-primary);
  }

  /* ---- Superscript / Subscript ---- */
  .md-render :global(sup),
  .md-render :global(sub) {
    font-size: 9px;
    line-height: 0;
  }
</style>
