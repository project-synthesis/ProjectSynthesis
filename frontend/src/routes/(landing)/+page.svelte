<script lang="ts">
  import Logo from '$lib/components/shared/Logo.svelte';
  import Navbar from '$lib/components/landing/Navbar.svelte';
  import Footer from '$lib/components/landing/Footer.svelte';
  import { base } from '$app/paths';

  // ---- Scroll-triggered animation via IntersectionObserver ----
  $effect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            (entry.target as HTMLElement).classList.add('in-view');
          } else {
            (entry.target as HTMLElement).classList.remove('in-view');
          }
        }
      },
      { threshold: 0.15, rootMargin: '0px 0px -40px 0px' }
    );

    requestAnimationFrame(() => {
      const targets = document.querySelectorAll<HTMLElement>('[data-animate]');
      targets.forEach((el) => observer.observe(el));
    });

    return () => observer.disconnect();
  });

  // ---- Hero mockup dimension colors ----
  const dimColors = [
    'var(--color-neon-cyan)',
    'var(--color-neon-purple)',
    'var(--color-neon-green)',
    'var(--color-neon-yellow)',
    'var(--color-neon-pink)',
  ];

  // ---- Score comparison data ----
  const scores = [
    { dim: 'Clarity', before: 3.2, after: 8.1, delta: 4.9, color: 'var(--color-neon-cyan)' },
    { dim: 'Specificity', before: 2.0, after: 8.8, delta: 6.8, color: 'var(--color-neon-purple)' },
    { dim: 'Structure', before: 2.2, after: 9.0, delta: 6.8, color: 'var(--color-neon-green)' },
    { dim: 'Faithfulness', before: 5.0, after: 8.4, delta: 3.4, color: 'var(--color-neon-yellow)' },
    { dim: 'Conciseness', before: 8.0, after: 7.2, delta: -0.8, color: 'var(--color-neon-pink)' },
  ];

  // Simple Icons CDN — CC0 licensed brand SVGs rendered monochrome
  // Enough items so duplicates are never visible simultaneously
  const ideLogos = [
    { name: 'Claude Code', slug: 'claude' },
    { name: 'Cursor', slug: 'cursor' },
    { name: 'Windsurf', slug: 'windsurf' },
    { name: 'Zed', slug: 'zedindustries' },
    { name: 'JetBrains', slug: 'jetbrains' },
    { name: 'Neovim', slug: 'neovim' },
    { name: 'Sublime Text', slug: 'sublimetext' },
    { name: 'GitHub Copilot', slug: 'githubcopilot' },
    { name: 'Eclipse', slug: 'eclipseide' },
    { name: 'Android Studio', slug: 'androidstudio' },
    { name: 'Vim', slug: 'vim' },
    { name: 'Replit', slug: 'replit' },
  ];
</script>

<Navbar />

<main id="main-content">
  <!-- ============================================================ -->
  <!-- SECTION 1: HERO                                              -->
  <!-- ============================================================ -->
  <section id="hero" class="hero" aria-labelledby="hero-heading">
    <div class="hero__container">
      <div class="hero__content">
        <h1 id="hero-heading" class="hero__headline" data-animate style="--delay:100ms;">
          Prompts In.<br/>
          <span style="color: var(--color-neon-cyan);">Better Prompts Out.</span>
        </h1>
        <p class="hero__subheading" data-animate style="--delay:250ms;">
          AI-powered prompt optimization pipeline. Analyze, rewrite, and score — completely free. No API key. No subscription. Your IDE's model does the work.
        </p>
        <div class="hero__actions" data-animate style="--delay:400ms;">
          <a href="https://github.com/project-synthesis/ProjectSynthesis" class="btn-primary" target="_blank" rel="noopener">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
            View on GitHub
          </a>
          <a href="#example" class="btn-ghost">See It Work</a>
        </div>
      </div>

      <div class="hero__preview" data-animate style="--delay:150ms;">
        <div class="hero__mockup">
          <div class="mockup__bar" aria-hidden="true">
            <span class="mockup__dot" style="background:var(--color-neon-red);"></span>
            <span class="mockup__dot" style="background:var(--color-neon-yellow);"></span>
            <span class="mockup__dot" style="background:var(--color-neon-green);"></span>
            <span class="mockup__title">pipeline &mdash; synthesis</span>
          </div>
          <div class="mockup__body">
            <div class="mockup__phase" style="animation-delay:800ms;">
              <span class="mockup__badge" style="color:var(--color-neon-cyan);border-color:var(--color-neon-cyan);">ANALYZE</span>
              <span class="mockup__phase-text">task_type: coding &middot; strategy: chain-of-thought</span>
            </div>
            <div class="mockup__phase" style="animation-delay:1300ms;">
              <span class="mockup__badge" style="color:var(--color-neon-purple);border-color:var(--color-neon-purple);">OPTIMIZE</span>
              <span class="mockup__phase-text">+structure +constraints +examples &middot; 3 weaknesses addressed</span>
            </div>
            <div class="mockup__phase" style="animation-delay:1800ms;">
              <span class="mockup__badge" style="color:var(--color-neon-green);border-color:var(--color-neon-green);">SCORE</span>
              <span class="mockup__phase-score">
                <span style="color:var(--color-neon-green);">8.4</span>
                <span class="mockup__phase-text">/10 &middot;</span>
                <span class="font-mono" style="color:var(--color-neon-green);font-size:10px;">+2.1</span>
              </span>
            </div>
            <div class="mockup__mini-bars">
              {#each ['clarity', 'specificity', 'structure', 'faithful.', 'concise.'] as dim, i}
                <div class="mockup__mini-row">
                  <span class="mockup__mini-label">{dim}</span>
                  <div class="mockup__mini-track">
                    <div class="mockup__mini-fill" style="width:{62 + i * 7}%;background:{dimColors[i]};"></div>
                  </div>
                  <span class="font-mono mockup__mini-val" style="color:{dimColors[i]};">{(6.2 + i * 0.7).toFixed(1)}</span>
                </div>
              {/each}
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- SECTION 2: PIPELINE DEEP-DIVE                                -->
  <!-- ============================================================ -->
  <section id="pipeline" class="pipeline-section" aria-labelledby="pipeline-heading">
    <h2 id="pipeline-heading" class="section-heading pipeline-heading">Three Phases. Zero Guesswork.</h2>

    <div class="pipeline-sticky">
      <div class="pipeline-grid">
        <!-- Phase 1: Analyze -->
        <div class="pipeline-phase" data-animate style="--delay:0ms;">
          <div class="pipeline-phase__header">
            <span class="font-mono pipeline-phase__number" style="color:var(--color-neon-cyan);">01</span>
            <h3 class="pipeline-phase__title" style="color:var(--color-neon-cyan);">ANALYZE</h3>
          </div>
          <p class="pipeline-phase__text">
            Classifies task type. Detects weaknesses. Selects from six optimization strategies. Confidence gate at 0.7 triggers automatic fallback.
          </p>
          <div class="pipeline-phase__tags">
            <span class="pipeline-tag" style="border-color:var(--color-neon-cyan);color:var(--color-neon-cyan);">coding</span>
            <span class="pipeline-chip">No constraints</span>
            <span class="pipeline-chip">No examples</span>
            <span class="pipeline-chip">No format spec</span>
          </div>
        </div>

        <!-- Phase 2: Optimize -->
        <div class="pipeline-phase" data-animate style="--delay:100ms;">
          <div class="pipeline-phase__header">
            <span class="font-mono pipeline-phase__number" style="color:var(--color-neon-purple);">02</span>
            <h3 class="pipeline-phase__title" style="color:var(--color-neon-purple);">OPTIMIZE</h3>
          </div>
          <p class="pipeline-phase__text">
            Rewrites using the selected strategy. Adds structure, constraints, and specificity. Injects codebase context when a repo is linked. Every word earns its place.
          </p>
          <div class="pipeline-phase__tags">
            <span class="pipeline-tag" style="border-color:var(--color-neon-purple);color:var(--color-neon-purple);">chain-of-thought</span>
            <span class="pipeline-chip">+structure</span>
            <span class="pipeline-chip">+constraints</span>
          </div>
        </div>

        <!-- Phase 3: Score -->
        <div class="pipeline-phase" data-animate style="--delay:200ms;">
          <div class="pipeline-phase__header">
            <span class="font-mono pipeline-phase__number" style="color:var(--color-neon-green);">03</span>
            <h3 class="pipeline-phase__title" style="color:var(--color-neon-green);">SCORE</h3>
          </div>
          <p class="pipeline-phase__text">
            Blind A/B evaluation. LLM scores blended with model-independent heuristics. Randomized presentation order prevents position bias. Z-score normalized when history exists.
          </p>
          <div class="pipeline-phase__tags">
            <div class="pipeline-score-bars">
              <div class="pipeline-score-bar" style="width:62%;background:var(--color-neon-green);"></div>
              <div class="pipeline-score-bar" style="width:70%;background:var(--color-neon-green);"></div>
              <div class="pipeline-score-bar" style="width:78%;background:var(--color-neon-green);"></div>
              <div class="pipeline-score-bar" style="width:84%;background:var(--color-neon-green);"></div>
              <div class="pipeline-score-bar" style="width:72%;background:var(--color-neon-green);"></div>
            </div>
            <span class="pipeline-tag" style="border-color:var(--color-neon-green);color:var(--color-neon-green);">8.4</span>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- CALLOUT: FREE & OPEN SOURCE                                  -->
  <!-- ============================================================ -->
  <section class="callout-bar" aria-label="Key benefits" data-animate>
    <div class="callout-bar__inner">
      <div class="callout-item">
        <span class="callout-item__icon" style="color:var(--color-neon-green);">&#10003;</span>
        <div class="callout-item__text"><strong>Completely free.</strong><br/>No subscription. No API key. No paid tier.</div>
      </div>
      <div class="callout-item">
        <span class="callout-item__icon" style="color:var(--color-neon-cyan);">&#10003;</span>
        <div class="callout-item__text"><strong>Your IDE's model does the work.</strong><br/>Cursor, Windsurf, Zed, JetBrains, Neovim — any MCP editor.</div>
      </div>
      <div class="callout-item">
        <span class="callout-item__icon" style="color:var(--color-neon-purple);">&#10003;</span>
        <div class="callout-item__text"><strong>Open source. Self-hosted.</strong><br/>Passthrough runs on your editor's existing model.</div>
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- SECTION 3: LIVE EXAMPLE                                      -->
  <!-- ============================================================ -->
  <section id="example" class="section" aria-labelledby="example-heading">
    <h2 id="example-heading" class="section-heading example-heading">Before and After.</h2>

    <div class="example-container">
      <div class="example-panels">
        <!-- Input panel -->
        <div class="example-panel example-panel--before">
          <span class="example-phase-label" style="color:var(--color-text-dim);">INPUT</span>
          <div class="example-prompt font-mono">Build a REST API for a todo app</div>
        </div>

        <!-- Output panel -->
        <div class="example-panel example-panel--after">
          <span class="example-phase-label" style="color:var(--color-neon-green);">OUTPUT</span>
          <div class="example-optimized">
            <p class="example-h2">## Task</p>
            <p class="example-line">Build a REST API for a todo application.</p>
            <p class="example-h2">## Endpoints</p>
            <p class="example-line">- POST /todos — Create a todo. Body: &#123; title: string, completed?: boolean &#125;</p>
            <p class="example-line">- GET /todos — List all todos. Query: ?completed=true|false for filtering</p>
            <p class="example-line">- GET /todos/:id — Get single todo. Return 404 if not found</p>
            <p class="example-line">- PATCH /todos/:id — Partial update. Accept any subset of &#123; title, completed &#125;</p>
            <p class="example-line">- DELETE /todos/:id — Delete. Return 204 on success, 404 if not found</p>
            <p class="example-h2">## Constraints</p>
            <p class="example-line">- Language: Python 3.12 with FastAPI</p>
            <p class="example-line">- Validation: Pydantic models for all request/response bodies</p>
            <p class="example-line">- Error handling: Return &#123; detail: string &#125; with appropriate HTTP status codes</p>
            <p class="example-line">- ID generation: UUID v4</p>
            <p class="example-line">- Storage: In-memory dict (no database required)</p>
            <p class="example-h2">## Output</p>
            <p class="example-line">- Complete, runnable Python file</p>
            <p class="example-line">- Include type hints on all functions</p>
            <p class="example-line">- Include docstrings on each endpoint</p>
          </div>
        </div>
      </div>

      <!-- Score comparison -->
      <div class="score-comparison" data-animate>
        <div class="score-grid">
          {#each scores as s}
            <div class="score-row">
              <span class="score-dim">{s.dim}</span>
              <span class="score-val font-mono" style="color:var(--color-text-dim);">{s.before.toFixed(1)}</span>
              <div class="score-bar-track">
                <div class="score-bar-before" style="width:{s.before * 10}%;"></div>
              </div>
              <div class="score-bar-track">
                <div class="score-bar-after" style="width:{s.after * 10}%;background:{s.color};"></div>
              </div>
              <span class="score-val font-mono" style="color:{s.color};">{s.after.toFixed(1)}</span>
              <span class="score-delta font-mono" class:score-delta--negative={s.delta < 0}>
                {s.delta > 0 ? '+' : ''}{s.delta.toFixed(1)}
              </span>
            </div>
          {/each}
        </div>
        <p class="score-caption">
          Five dimensions. Hybrid LLM + heuristic scoring. Blind A/B evaluation with randomized presentation order.
        </p>
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- SECTION 3.5: KNOWLEDGE GRAPH                                 -->
  <!-- ============================================================ -->
  <section id="knowledge-graph" class="section" aria-labelledby="kg-heading">
    <h2 id="kg-heading" class="section-heading example-heading">Self-Building Pattern Library</h2>
    <p class="trust-mission" style="margin-bottom: 32px; max-width: 720px;">
      Every optimization continuously teaches the system. Prompts are embedded, clustered, and mapped into a dense semantic knowledge graph. When you start a new prompt, the highest-scoring meta-patterns from similar past requests are matched and injected automatically.
    </p>

    <div class="example-container">
      <div class="example-panels">
        <!-- Visualization Panel -->
        <div class="example-panel example-panel--before" data-animate style="--delay:100ms; align-items: center; justify-content: center;">
          <!-- Mini Knowledge Graph SVG Base -->
          <svg width="100%" height="100%" viewBox="0 0 300 260" style="max-height: 240px; overflow: visible;">
            <!-- Edges (Straight, Exact Connections) -->
            <path d="M150,130 L206.5,73.5" fill="none" stroke="#00e5ff" stroke-width="0.5" opacity="0.3" />
            <path d="M150,130 L206.5,186.5" fill="none" stroke="#00e5ff" stroke-width="0.5" opacity="0.3" />
            <path d="M150,130 L81,90" fill="none" stroke="#00e5ff" stroke-width="0.5" opacity="0.3" />
            <path d="M150,130 L81,170" fill="none" stroke="#00e5ff" stroke-width="1.5" opacity="0.7" />

            <!-- Domain arcs (Perfect Mathematical Circle R=80) -->
            <!-- Frontend (Amber) -->
            <path d="M150,50 A80,80 0 0 1 230,130" fill="none" stroke="#f59e0b" stroke-width="1.5" opacity="0.3" />
            <!-- Database (Green) -->
            <path d="M230,130 A80,80 0 0 1 150,210" fill="none" stroke="#10b981" stroke-width="1.5" opacity="0.3" />
            <!-- Backend (Purple, Active) - Left Half -->
            <path d="M150,210 A80,80 0 0 1 70,130 A80,80 0 0 1 150,50" fill="none" stroke="#a855f7" stroke-width="2" opacity="0.8" />

            <!-- Labels -->
            <text x="50" y="133" fill="#a855f7" font-size="8" font-family="var(--font-mono)" font-weight="600" opacity="0.9" text-anchor="end">BACKEND</text>
            <text x="240" y="70" fill="#f59e0b" font-size="8" font-family="var(--font-mono)" opacity="0.5">FRONTEND</text>
            <text x="240" y="200" fill="#10b981" font-size="8" font-family="var(--font-mono)" opacity="0.5">DATABASE</text>

            <!-- Center Origin Node -->
            <circle cx="150" cy="130" r="16" fill="none" stroke="#00e5ff" stroke-width="1" stroke-dasharray="2 2" opacity="0.6" />
            <circle cx="150" cy="130" r="10" fill="#06060c" stroke="#00e5ff" stroke-width="1" />
            <text x="150" y="133" text-anchor="middle" fill="#00e5ff" font-size="8" font-family="var(--font-mono)">KG</text>

            <!-- Active Backend Node -->
            <circle cx="81" cy="170" r="16" fill="none" stroke="#a855f7" stroke-width="1" stroke-dasharray="2 2" opacity="0.6" />
            <circle cx="81" cy="170" r="10" fill="#06060c" stroke="#a855f7" stroke-width="2" />
            <circle cx="81" cy="170" r="4" fill="#a855f7" />
            <text x="81" y="196" text-anchor="middle" fill="#e4e4f0" font-size="9" font-family="var(--font-mono)">REST API Todo</text>

            <!-- Neighbor Nodes (Placed exactly on R=80) -->
            <circle cx="81" cy="90" r="5" fill="#06060c" stroke="#a855f7" stroke-width="1.5" />
            <circle cx="206.5" cy="73.5" r="5" fill="#06060c" stroke="#f59e0b" stroke-width="1.5" opacity="0.5" />
            <circle cx="206.5" cy="186.5" r="5" fill="#06060c" stroke="#10b981" stroke-width="1.5" opacity="0.5" />
          </svg>
        </div>

        <!-- Pattern Entry Panel -->
        <div class="example-panel example-panel--after" data-animate style="--delay:200ms;">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
            <div>
              <div class="example-phase-label" style="color: var(--color-neon-purple); margin-bottom: 4px;">PATTERN FAMILY MATCHED</div>
              <h3 style="font-family: var(--font-sans); font-size: 14px; font-weight: 600; color: var(--color-text-primary); margin: 0;">REST API for Todo App</h3>
            </div>
            <span class="pipeline-tag" style="color: var(--color-neon-purple); border-color: rgba(168, 85, 247, 0.4); font-weight: 600;">SIM 0.92</span>
          </div>
          
          <div class="pipeline-phase__tags" style="margin-bottom: 16px;">
            <span class="pipeline-tag" style="color: var(--color-neon-purple); border-color: rgba(168, 85, 247, 0.3);">BACKEND</span>
            <span class="pipeline-tag" style="color: var(--color-text-dim); border-color: var(--color-border-subtle);">CODING</span>
            <span class="pipeline-tag" style="color: var(--color-neon-green); border-color: rgba(0, 229, 255, 0.3);">AVG 8.4</span>
            <span class="pipeline-tag" style="color: var(--color-text-dim); border-color: var(--color-border-subtle);">USAGE 42</span>
          </div>

          <p class="example-line" style="margin-bottom: 12px; color: var(--color-text-secondary);">
            Extracting highest utility meta-patterns from related past optimizations...
          </p>

          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="padding: 10px 12px; border: 1px solid var(--color-border-subtle); background: var(--color-bg-primary); display: flex; gap: 10px; align-items: flex-start;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" style="color: var(--color-neon-cyan); margin-top: 1px; flex-shrink: 0;"><path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2"/></svg>
              <div>
                <p class="example-line" style="color: var(--color-text-primary); font-weight: 500; font-size: 11px; margin-bottom: 2px;">Explicit Endpoint Schemas</p>
                <p class="example-line" style="font-size: 10px; color: var(--color-text-dim); line-height: 1.4;">Defines exact constraints for HTTP verbs, error states, and strict Pydantic payload models.</p>
              </div>
            </div>
            
            <div style="padding: 10px 12px; border: 1px solid var(--color-border-subtle); background: var(--color-bg-primary); display: flex; gap: 10px; align-items: flex-start;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" style="color: var(--color-neon-cyan); margin-top: 1px; flex-shrink: 0;"><path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2"/></svg>
              <div>
                <p class="example-line" style="color: var(--color-text-primary); font-weight: 500; font-size: 11px; margin-bottom: 2px;">Stateless UUID Generation</p>
                <p class="example-line" style="font-size: 10px; color: var(--color-text-dim); line-height: 1.4;">Always constrain database or dict ID keys to UUID structure for predictable system context.</p>
              </div>
            </div>

            <div style="padding: 10px 12px; border: 1px solid var(--color-border-subtle); background: var(--color-bg-primary); display: flex; gap: 10px; align-items: flex-start;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" style="color: var(--color-neon-cyan); margin-top: 1px; flex-shrink: 0;"><path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2"/></svg>
              <div>
                <p class="example-line" style="color: var(--color-text-primary); font-weight: 500; font-size: 11px; margin-bottom: 2px;">Enforced Type Definitions</p>
                <p class="example-line" style="font-size: 10px; color: var(--color-text-dim); line-height: 1.4;">Strict PEP 484 type hints + verbose response docstrings required on all generated targets.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- SECTION 4: WORKS EVERYWHERE                                  -->
  <!-- ============================================================ -->
  <section id="integrations" class="section" aria-labelledby="integrations-heading">
    <h2 id="integrations-heading" class="section-heading integrations-heading">Free. Forever. In Every Editor.</h2>

    <div class="integrations-container">
      <div class="integrations-grid">
        <!-- Tier 1: Zero Config -->
        <div class="integration-card" data-animate style="--delay:0ms;">
          <div class="integration-icon" style="color:var(--color-neon-cyan);">
            <svg width="28" height="28" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M11 2L5 11h4l-2 7 8-9h-5l3-7z" stroke="currentColor" stroke-width="1.2"/></svg>
          </div>
          <h3 class="integration-title">Zero Cost</h3>
          <p class="integration-desc">No subscription required. No API key. The MCP passthrough runs the pipeline through your IDE's existing model — you pay nothing extra.</p>
        </div>

        <!-- Tier 2: Your IDE, Your LLM -->
        <div class="integration-card" data-animate style="--delay:100ms;">
          <div class="integration-icon" style="color:var(--color-neon-purple);">
            <svg width="28" height="28" viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="2" y="3" width="16" height="14" rx="1" stroke="currentColor" stroke-width="1.2"/><path d="M5 8l3 2.5L5 13M10 13h5" stroke="currentColor" stroke-width="1.2"/></svg>
          </div>
          <h3 class="integration-title">Your IDE, Your Model</h3>
          <p class="integration-desc">Drop a single config file into your workspace. Your editor's built-in model runs the optimization — Synthesis orchestrates the pipeline, scores the result, tracks everything.</p>
        </div>

        <!-- Tier 3: Codebase-Aware -->
        <div class="integration-card" data-animate style="--delay:200ms;">
          <div class="integration-icon" style="color:var(--color-neon-green);">
            <svg width="28" height="28" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="6" cy="6" r="2" stroke="currentColor" stroke-width="1.2"/><circle cx="6" cy="14" r="2" stroke="currentColor" stroke-width="1.2"/><circle cx="14" cy="10" r="2" stroke="currentColor" stroke-width="1.2"/><path d="M6 8v4M8 6h4a2 2 0 0 1 2 2v0" stroke="currentColor" stroke-width="1.2"/></svg>
          </div>
          <h3 class="integration-title">Codebase-Aware Optimization</h3>
          <p class="integration-desc">Link a GitHub repo and the optimizer learns your conventions. Function signatures, error handling patterns, naming standards, architecture decisions — optimized prompts reference YOUR code, not generic examples.</p>
        </div>
      </div>

      <!-- Logo strip — Simple Icons CDN (CC0 licensed) -->
      <div class="logo-strip" aria-label="Supported editors">
        <div class="logo-strip__track">
          {#each [0, 1] as _copy}
            <div class="logo-strip__set" aria-hidden={_copy === 1 ? 'true' : undefined}>
              {#each ideLogos as ide}
                <span class="logo-strip__badge">
                  <img
                    class="logo-strip__img"
                    src="https://cdn.simpleicons.org/{ide.slug}/8b8ba8"
                    alt=""
                    width="14"
                    height="14"
                    loading="lazy"
                  />
                  <span class="logo-strip__name font-mono">{ide.name}</span>
                </span>
              {/each}
            </div>
          {/each}
        </div>
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- SECTION 5: GET STARTED + TRUST                               -->
  <!-- ============================================================ -->
  <section id="trust" class="trust-section" aria-labelledby="cta-heading">
    <p class="trust-mission">
      Built by engineers who got tired of vague prompts. Apache 2.0 licensed. No telemetry. No cloud dependency. Your prompts never leave your infrastructure.
    </p>

    <div class="trust-badges">
      <a href="{base}/security" class="trust-badge">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="3" y="7" width="10" height="7" rx="1" stroke="currentColor" stroke-width="1.2"/><path d="M5 7V5a3 3 0 0 1 6 0v2" stroke="currentColor" stroke-width="1.2"/></svg>
        <span>Encrypted at rest</span>
      </a>
      <a href="{base}/privacy" class="trust-badge">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2 8s2.5-4 6-4 6 4 6 4-2.5 4-6 4-6-4-6-4z" stroke="currentColor" stroke-width="1.2"/><circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.2"/><path d="M3 13L13 3" stroke="currentColor" stroke-width="1.2"/></svg>
        <span>Zero telemetry</span>
      </a>
      <a href="{base}/terms" class="trust-badge">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 2h5l3 3v9H4V2z" stroke="currentColor" stroke-width="1.2"/><path d="M9 2v3h3M6 8h4M6 10.5h4" stroke="currentColor" stroke-width="1.2"/></svg>
        <span>Apache 2.0</span>
      </a>
      <a href="{base}/privacy" class="trust-badge">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="2" y="2" width="12" height="4" rx="1" stroke="currentColor" stroke-width="1.2"/><rect x="2" y="10" width="12" height="4" rx="1" stroke="currentColor" stroke-width="1.2"/><path d="M2 6v4M14 6v4" stroke="currentColor" stroke-width="1.2"/><circle cx="11" cy="4" r="0.5" fill="currentColor"/><circle cx="11" cy="12" r="0.5" fill="currentColor"/></svg>
        <span>Self-hosted</span>
      </a>
    </div>

    <div class="trust-cta" data-animate>
      <h2 id="cta-heading" class="trust-cta__headline">
        <span class="">STOP GUESSING. START MEASURING.</span>
      </h2>
      <p class="trust-cta__sub">
        Every prompt scored. Every improvement tracked. Every iteration versioned.
      </p>
      <a href="https://github.com/project-synthesis/ProjectSynthesis" class="btn-primary" target="_blank" rel="noopener">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
        View on GitHub
      </a>
    </div>
  </section>
</main>

<Footer />

<style>
  /* ================================================================
     ANIMATION SYSTEM — IntersectionObserver driven
     ================================================================ */
  :global([data-animate]) {
    opacity: 0;
  }

  :global([data-animate].in-view) {
    animation: reveal-spring 0.8s var(--ease-spring) forwards;
    animation-delay: var(--delay, 0ms);
  }

  @keyframes reveal-spring {
    from {
      opacity: 0;
      transform: translateY(24px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  /* ================================================================
     BUTTONS — shared base + variants
     ================================================================ */
  .btn-primary,
  .btn-ghost {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 24px;
    padding: 0 12px;
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-sans);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border: 1px solid var(--color-neon-cyan);
    text-decoration: none;
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .btn-primary:hover,
  .btn-ghost:hover {
    transform: translateY(-1px);
  }

  .btn-primary {
    color: var(--color-bg-primary);
    background: var(--color-neon-cyan);
  }

  .btn-primary:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 85%, white);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 85%, white);
  }

  .btn-ghost {
    color: var(--color-neon-cyan);
    background: transparent;
  }

  .btn-ghost:hover {
    background: rgba(0, 229, 255, 0.08);
  }

  /* ================================================================
     SECTION 1: HERO
     ================================================================ */
  .hero {
    padding: 64px 16px 32px;
  }

  .hero__container {
    max-width: 1120px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 40px;
    align-items: center;
  }

  .hero__headline {
    font-family: var(--font-display);
    font-weight: 700;
    font-size: clamp(24px, 4vw, 40px);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    line-height: 1.15;
    margin: 0 0 12px 0;
    color: var(--color-text-primary);
  }

  .hero__subheading {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0 0 16px 0;
    line-height: 1.6;
    max-width: 420px;
  }

  .hero__actions {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .hero__preview {
    display: flex;
    justify-content: flex-end;
  }

  /* ---- Hero Mockup ---- */
  .hero__mockup {
    width: 100%;
    max-width: 480px;
    border: 1px solid var(--color-border-accent);
    background: var(--color-bg-card);
    overflow: hidden;
  }

  .mockup__bar {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 6px 8px;
    border-bottom: 1px solid var(--color-border-subtle);
    background: var(--color-bg-secondary);
  }

  .mockup__dot {
    width: 6px;
    height: 6px;
    border-radius: 0;
    opacity: 0.7;
  }

  .mockup__title {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    margin-left: 8px;
  }

  .mockup__body {
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .mockup__phase {
    display: flex;
    align-items: center;
    gap: 6px;
    opacity: 0;
    animation: phase-type-in 400ms var(--ease-spring) both;
  }

  .mockup__badge {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    padding: 0 4px;
    border: 1px solid;
    flex-shrink: 0;
  }

  .mockup__phase-text {
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .mockup__phase-score {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 10px;
  }

  .mockup__mini-bars {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-top: 4px;
    padding-top: 6px;
    border-top: 1px solid var(--color-border-subtle);
  }

  .mockup__mini-row {
    display: grid;
    grid-template-columns: 56px 1fr 28px;
    align-items: center;
    gap: 6px;
  }

  .mockup__mini-label {
    color: var(--color-text-dim);
    font-size: 9px;
  }

  .mockup__mini-track {
    height: 3px;
    background: rgba(74, 74, 106, 0.15);
    overflow: hidden;
  }

  .mockup__mini-fill {
    height: 100%;
    transition: width 800ms var(--ease-spring);
  }

  .mockup__mini-val {
    font-size: 9px;
    color: var(--color-neon-cyan);
  }

  /* ================================================================
     SECTION 2: PIPELINE DEEP-DIVE
     ================================================================ */
  .pipeline-section {
    padding: 40px 16px;
  }

  .pipeline-heading {
    text-align: center;
    padding: 0 0 24px;
  }

  .pipeline-sticky {
    max-width: 1120px;
    margin: 0 auto;
  }

  .pipeline-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    align-items: stretch;
  }

  .pipeline-phase {
    padding: 16px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    display: flex;
    flex-direction: column;
  }

  .pipeline-phase__header {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 8px;
  }

  .pipeline-phase__number {
    font-size: 18px;
    font-weight: 700;
    line-height: 1;
  }

  .pipeline-phase__title {
    font-family: var(--font-display);
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin: 0;
  }

  .pipeline-phase__text {
    font-size: 12px;
    color: var(--color-text-secondary);
    line-height: 1.6;
    margin: 0 0 10px 0;
    flex: 1;
  }

  .pipeline-phase__tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
  }

  .pipeline-tag {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    padding: 1px 6px;
    border: 1px solid;
  }

  .pipeline-chip {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 1px 6px;
    border: 1px solid var(--color-border-subtle);
  }

  .pipeline-score-bars {
    display: flex;
    gap: 2px;
    align-items: flex-end;
    height: 20px;
  }

  .pipeline-score-bar {
    width: 4px;
    height: 100%;
  }

  /* Pipeline mobile layout */
  @media (max-width: 768px) {
    .pipeline-grid {
      grid-template-columns: 1fr;
      gap: 8px;
    }
  }

  /* ================================================================
     SECTION 3: LIVE EXAMPLE
     ================================================================ */
  .section {
    padding: 40px 16px;
  }

  .example-heading {
    text-align: center;
    margin-bottom: 24px;
  }

  /* ================================================================
     CALLOUT BAR
     ================================================================ */
  .callout-bar {
    padding: 20px 16px;
    border-top: 1px solid var(--color-border-subtle);
    border-bottom: 1px solid var(--color-border-subtle);
    background: var(--color-bg-secondary);
  }

  .callout-bar__inner {
    max-width: 1120px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }

  .callout-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px;
  }

  .callout-item__icon {
    font-size: 14px;
    font-weight: 700;
    flex-shrink: 0;
  }

  .callout-item__text {
    font-size: 12px;
    color: var(--color-text-secondary);
    line-height: 1.4;
  }

  .callout-item__text strong {
    color: var(--color-text-primary);
    font-weight: 600;
  }

  /* ================================================================
     SECTION 3: LIVE EXAMPLE (cont.)
     ================================================================ */
  .example-container {
    max-width: 1120px;
    margin: 0 auto;
  }

  .example-panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 16px;
    align-items: stretch;
  }

  .example-panel {
    padding: 12px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .example-panel--before {
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
  }

  .example-panel--after {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-accent);
  }

  .example-prompt {
    font-size: 12px;
    color: var(--color-text-dim);
    margin-bottom: 10px;
    line-height: 1.5;
  }

  .example-phase-label {
    font-family: var(--font-display);
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 8px;
  }

  .example-optimized {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .example-h2 {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--color-neon-cyan);
    margin: 6px 0 2px 0;
  }

  .example-h2:first-child {
    margin-top: 0;
  }

  .example-line {
    font-size: 11px;
    color: var(--color-text-secondary);
    margin: 0;
    line-height: 1.5;
  }

  /* ---- Score Comparison ---- */
  .score-comparison {
    max-width: 720px;
    margin: 0 auto;
  }

  .score-grid {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .score-row {
    display: grid;
    grid-template-columns: 80px 30px 1fr 1fr 30px 44px;
    align-items: center;
    gap: 6px;
    height: 22px;
  }

  .score-dim {
    font-size: 10px;
    color: var(--color-text-dim);
    text-align: right;
  }

  .score-val {
    font-size: 9px;
    text-align: center;
  }

  .score-bar-track {
    height: 6px;
    background: rgba(74, 74, 106, 0.15);
    overflow: hidden;
  }

  .score-bar-before {
    height: 100%;
    background: rgba(74, 74, 106, 0.5);
  }

  .score-bar-after {
    height: 100%;
  }

  .score-delta {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    color: var(--color-neon-green);
    text-align: right;
  }

  .score-delta--negative {
    color: var(--color-neon-red);
  }

  .score-caption {
    font-size: 10px;
    color: var(--color-text-dim);
    text-align: center;
    margin: 12px 0 0 0;
  }

  /* ================================================================
     SECTION 4: WORKS EVERYWHERE
     ================================================================ */
  .integrations-heading {
    text-align: center;
    margin-bottom: 24px;
  }

  .integrations-container {
    max-width: 1120px;
    margin: 0 auto;
  }

  .integrations-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin-bottom: 16px;
    align-items: stretch;
  }

  .integration-card {
    padding: 16px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    transition: all var(--duration-hover) var(--ease-spring);
    display: flex;
    flex-direction: column;
  }

  .integration-card:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .integration-icon {
    margin-bottom: 10px;
    line-height: 0;
  }

  .integration-title {
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0 0 4px 0;
  }

  .integration-desc {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0;
    line-height: 1.5;
    flex: 1;
  }

  /* ---- Logo Strip ---- */
  .logo-strip {
    overflow: hidden;
    border-top: 1px solid var(--color-border-subtle);
    border-bottom: 1px solid var(--color-border-subtle);
    padding: 12px 0;
  }

  .logo-strip__track {
    display: flex;
    width: max-content;
    animation: scroll-logos 50s linear infinite;
  }

  .logo-strip__set {
    display: flex;
    gap: 20px;
    padding-right: 20px;
    flex-shrink: 0;
  }

  @keyframes scroll-logos {
    from { transform: translateX(0); }
    to { transform: translateX(-50%); }
  }

  .logo-strip__badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border: 1px solid var(--color-border-subtle);
    white-space: nowrap;
    flex-shrink: 0;
    transition: border-color var(--duration-hover) var(--ease-spring);
  }

  .logo-strip__badge:hover {
    border-color: var(--color-border-accent);
  }

  .logo-strip__img {
    opacity: 0.7;
    flex-shrink: 0;
  }

  .logo-strip__badge:hover .logo-strip__img {
    opacity: 1;
  }

  .logo-strip__name {
    font-size: 11px;
    font-weight: 500;
    color: var(--color-text-secondary);
    letter-spacing: 0.02em;
  }

  /* ================================================================
     SECTION 5: GET STARTED + TRUST
     ================================================================ */
  .trust-section {
    padding: 40px 16px;
    background: var(--color-bg-secondary);
    border-top: 1px solid; border-color: var(--color-neon-cyan);
  }

  .trust-mission {
    font-size: 12px;
    color: var(--color-text-secondary);
    text-align: center;
    max-width: 640px;
    margin: 0 auto 16px;
    line-height: 1.6;
  }

  .trust-badges {
    display: flex;
    justify-content: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 24px;
  }

  .trust-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px;
    border: 1px solid var(--color-border-subtle);
    text-decoration: none;
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .trust-badge span {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .trust-badge svg {
    color: var(--color-text-dim);
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .trust-badge:hover {
    border-color: var(--color-border-accent);
  }

  .trust-badge:hover span,
  .trust-badge:hover svg {
    color: var(--color-text-secondary);
  }

  .trust-cta {
    max-width: 560px;
    margin: 0 auto;
    text-align: center;
  }

  .trust-cta__headline {
    font-family: var(--font-display);
    font-weight: 700;
    font-size: clamp(18px, 3vw, 28px);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    line-height: 1.2;
    margin: 0 0 8px 0;
  }

  .trust-cta__sub {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0 0 16px 0;
  }

  /* ================================================================
     RESPONSIVE
     ================================================================ */
  @media (max-width: 1024px) {
    .hero__container {
      grid-template-columns: 1fr;
      gap: 24px;
    }

    .hero__preview {
      justify-content: center;
    }

    .hero__mockup {
      max-width: 100%;
    }

    .example-panels {
      grid-template-columns: 1fr;
    }

    .integrations-grid {
      grid-template-columns: 1fr;
    }

    .callout-bar__inner {
      grid-template-columns: 1fr;
    }
  }

  @media (max-width: 640px) {
    .hero {
      padding: 52px 16px 24px;
    }

    .hero__headline {
      font-size: 22px;
    }

    .score-row {
      grid-template-columns: 60px 24px 1fr 1fr 24px 36px;
      gap: 3px;
    }
  }
</style>
