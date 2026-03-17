<script lang="ts">
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
            observer.unobserve(entry.target);
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

  // ---- Score comparison data ----
  const scores = [
    { dim: 'Clarity', before: 3.2, after: 8.1, delta: 4.9, color: 'var(--color-neon-cyan)' },
    { dim: 'Specificity', before: 2.0, after: 8.8, delta: 6.8, color: 'var(--color-neon-purple)' },
    { dim: 'Structure', before: 2.2, after: 9.0, delta: 6.8, color: 'var(--color-neon-green)' },
    { dim: 'Faithfulness', before: 5.0, after: 8.4, delta: 3.4, color: 'var(--color-neon-yellow)' },
    { dim: 'Conciseness', before: 8.0, after: 7.2, delta: -0.8, color: 'var(--color-neon-pink)' },
  ];

  const ideLabels = ['Claude Code', 'Cursor', 'Windsurf', 'VS Code', 'Zed', 'JetBrains'];
</script>

<Navbar />

<main id="main-content">
  <!-- ============================================================ -->
  <!-- SECTION 1: HERO                                              -->
  <!-- ============================================================ -->
  <section id="hero" class="hero" aria-labelledby="hero-heading">
    <div class="hero__container">
      <div class="hero__content" data-animate style="--delay:200ms;">
        <h1 id="hero-heading" class="hero__headline">
          Prompts In.<br/>
          <span class="text-gradient-forge">Better Prompts Out.</span>
        </h1>
        <p class="hero__subheading">
          AI-powered prompt optimization pipeline. Analyze, rewrite, and score — with or without an API key. Self-hosted. Open source. Measurably better.
        </p>
        <div class="hero__actions">
          <a href="https://github.com/project-synthesis/ProjectSynthesis" class="btn-primary" target="_blank" rel="noopener">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
            View on GitHub
          </a>
          <a href="#example" class="btn-ghost">See It Work</a>
        </div>
      </div>

      <div class="hero__preview" data-animate style="--delay:500ms;">
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
                    <div class="mockup__mini-fill" style="width:{62 + i * 7}%;background:var(--color-neon-cyan);"></div>
                  </div>
                  <span class="font-mono mockup__mini-val">{(6.2 + i * 0.7).toFixed(1)}</span>
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
        <div class="pipeline-phase" data-reveal>
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
        <div class="pipeline-phase" data-reveal>
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
        <div class="pipeline-phase" data-reveal>
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
  <!-- SECTION 3: LIVE EXAMPLE                                      -->
  <!-- ============================================================ -->
  <section id="example" class="section" aria-labelledby="example-heading">
    <h2 id="example-heading" class="section-heading example-heading">Before and After.</h2>

    <div class="example-container">
      <div class="example-panels">
        <!-- Before panel -->
        <div class="example-panel example-panel--before">
          <div class="example-prompt font-mono">Build a REST API for a todo app</div>
          <div class="example-analyzer">
            <span class="example-badge" style="border-color:var(--color-neon-cyan);color:var(--color-neon-cyan);">task_type: coding</span>
            <div class="example-weaknesses">
              <span class="example-weakness">No language specified</span>
              <span class="example-weakness">No endpoint signatures</span>
              <span class="example-weakness">No error handling</span>
              <span class="example-weakness">No response format</span>
              <span class="example-weakness">No auth requirements</span>
              <span class="example-weakness">No validation rules</span>
            </div>
            <span class="example-badge" style="border-color:var(--color-neon-purple);color:var(--color-neon-purple);">strategy: structured-output</span>
            <span class="font-mono example-confidence">confidence: 0.92</span>
          </div>
        </div>

        <!-- After panel -->
        <div class="example-panel example-panel--after">
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
      <div class="score-comparison" data-reveal>
        <div class="score-grid">
          {#each scores as s}
            <div class="score-row">
              <span class="score-dim">{s.dim}</span>
              <div class="score-bar-track">
                <div class="score-bar-before" style="width:{s.before * 10}%;"></div>
              </div>
              <div class="score-bar-track">
                <div class="score-bar-fill" style="width:{s.after * 10}%;background:{s.color};"></div>
              </div>
              <span class="score-delta" class:score-delta--negative={s.delta < 0}>
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
  <!-- SECTION 4: WORKS EVERYWHERE                                  -->
  <!-- ============================================================ -->
  <section id="integrations" class="section" aria-labelledby="integrations-heading">
    <h2 id="integrations-heading" class="section-heading integrations-heading">No Vendor Lock-In. No API Key Required.</h2>

    <div class="integrations-container">
      <div class="integrations-grid">
        <!-- Tier 1: Zero Config -->
        <div class="integration-card" data-animate style="--delay:0ms;">
          <div class="integration-icon" style="color:var(--color-neon-cyan);">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path d="M10 2v6M10 12v6M6 6l4-4 4 4M6 14l4 4 4-4" stroke="currentColor" stroke-width="1.2"/>
            </svg>
          </div>
          <h3 class="integration-title">Zero Config</h3>
          <p class="integration-desc">Works with Claude CLI out of the box. Max subscription means zero marginal cost per optimization. No API key, no billing, no setup.</p>
        </div>

        <!-- Tier 2: Your IDE, Your LLM -->
        <div class="integration-card" data-animate style="--delay:100ms;">
          <div class="integration-icon" style="color:var(--color-neon-purple);">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <circle cx="5" cy="5" r="2" stroke="currentColor" stroke-width="1.2"/>
              <circle cx="15" cy="5" r="2" stroke="currentColor" stroke-width="1.2"/>
              <circle cx="10" cy="15" r="2" stroke="currentColor" stroke-width="1.2"/>
              <path d="M6.5 6.5L9 13M13.5 6.5L11 13M7 5h6" stroke="currentColor" stroke-width="1"/>
            </svg>
          </div>
          <h3 class="integration-title">Your IDE, Your LLM</h3>
          <p class="integration-desc">Drop the pipeline into your editor. Your IDE's model does the optimization — Synthesis orchestrates the phases, scores the result, tracks the history.</p>
        </div>

        <!-- Tier 3: Codebase-Aware -->
        <div class="integration-card" data-animate style="--delay:200ms;">
          <div class="integration-icon" style="color:var(--color-neon-green);">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path d="M6 3v14M14 3v8M6 10h8M14 11l3 3-3 3" stroke="currentColor" stroke-width="1.2"/>
            </svg>
          </div>
          <h3 class="integration-title">Codebase-Aware Optimization</h3>
          <p class="integration-desc">Link a GitHub repo and the optimizer learns your conventions. Function signatures, error handling patterns, naming standards, architecture decisions — optimized prompts reference YOUR code, not generic examples.</p>
        </div>
      </div>

      <!-- Logo strip -->
      <div class="logo-strip" aria-label="Supported editors">
        <div class="logo-strip__inner">
          {#each [...ideLabels, ...ideLabels] as label}
            <span class="logo-strip__label font-mono" aria-hidden={ideLabels.indexOf(label) >= ideLabels.length ? 'true' : undefined}>{label}</span>
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
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1L3 3.5V7c0 3.5 2.1 6.4 5 7.5 2.9-1.1 5-4 5-7.5V3.5L8 1z" stroke="currentColor" stroke-width="1.2"/><path d="M6 8l1.5 1.5L10 6" stroke="currentColor" stroke-width="1.2"/></svg>
        <span>Encrypted at rest</span>
      </a>
      <a href="{base}/privacy" class="trust-badge">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="7" r="3" stroke="currentColor" stroke-width="1.2"/><path d="M1 7h2.5M12.5 7H15M3 3l2 2M11 11l2 2M3 11l2-2M11 3l2 2" stroke="currentColor" stroke-width="1.2"/><path d="M2 13l12-1" stroke="currentColor" stroke-width="1.2"/></svg>
        <span>Zero telemetry</span>
      </a>
      <a href="{base}/terms" class="trust-badge">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 2L2 8l6 6 6-6-6-6z" stroke="currentColor" stroke-width="1.2"/><path d="M5 8h6M8 5v6" stroke="currentColor" stroke-width="1"/></svg>
        <span>Apache 2.0</span>
      </a>
      <a href="{base}/privacy" class="trust-badge">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 3h10v10H3z" stroke="currentColor" stroke-width="1.2"/><path d="M6 1v2M10 1v2M6 13v2M10 13v2M1 6h2M1 10h2M13 6h2M13 10h2" stroke="currentColor" stroke-width="1"/></svg>
        <span>Self-hosted</span>
      </a>
    </div>

    <div class="trust-cta" data-animate>
      <h2 id="cta-heading" class="trust-cta__headline">
        <span class="text-gradient-forge">STOP GUESSING. START MEASURING.</span>
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
    transform: translateY(16px);
    transition:
      opacity var(--duration-structural) var(--ease-spring),
      transform var(--duration-structural) var(--ease-spring);
    transition-delay: var(--delay, 0ms);
  }

  :global([data-animate].in-view) {
    opacity: 1;
    transform: translateY(0);
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
    border-radius: 9999px;
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
    height: 300vh;
    position: relative;
    padding: 0 16px;
  }

  .pipeline-heading {
    text-align: center;
    padding: 40px 0 24px;
  }

  .pipeline-sticky {
    position: sticky;
    top: 0;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .pipeline-grid {
    max-width: 1120px;
    width: 100%;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
  }

  .pipeline-phase {
    padding: 16px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
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

  /* Pipeline fallbacks */
  @media (max-width: 768px) {
    .pipeline-section {
      height: auto !important;
      padding: 40px 16px;
    }
    .pipeline-sticky {
      position: static !important;
      height: auto !important;
    }
    .pipeline-grid {
      grid-template-columns: 1fr;
      gap: 8px;
    }
  }

  @supports not (animation-timeline: scroll()) {
    .pipeline-section {
      height: auto;
      padding: 40px 16px;
    }
    .pipeline-sticky {
      position: static;
      height: auto;
    }
    .pipeline-phase {
      opacity: 1;
      transform: none;
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

  .example-container {
    max-width: 1120px;
    margin: 0 auto;
  }

  .example-panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 16px;
  }

  .example-panel {
    padding: 12px;
    overflow: hidden;
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

  .example-analyzer {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
    border: 1px solid var(--color-border-subtle);
  }

  .example-badge {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    padding: 1px 6px;
    border: 1px solid;
    width: fit-content;
  }

  .example-weaknesses {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .example-weakness {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 1px 6px;
    border: 1px solid var(--color-border-subtle);
  }

  .example-confidence {
    font-size: 10px;
    color: var(--color-text-dim);
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
    grid-template-columns: 70px 1fr 1fr 50px;
    align-items: center;
    gap: 6px;
  }

  .score-dim {
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .score-bar-track {
    height: 4px;
    background: rgba(74, 74, 106, 0.15);
    overflow: hidden;
  }

  .score-bar-before {
    height: 100%;
    background: rgba(74, 74, 106, 0.4);
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
  }

  .integration-card {
    padding: 12px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .integration-card:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .integration-icon {
    margin-bottom: 8px;
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
  }

  /* ---- Logo Strip ---- */
  .logo-strip {
    overflow: hidden;
    border-top: 1px solid var(--color-border-subtle);
    border-bottom: 1px solid var(--color-border-subtle);
    padding: 8px 0;
  }

  .logo-strip__inner {
    display: flex;
    gap: 40px;
    animation: scroll-logos 30s linear infinite;
    width: max-content;
  }

  .logo-strip__label {
    font-size: 11px;
    color: var(--color-text-dim);
    white-space: nowrap;
    flex-shrink: 0;
  }

  /* ================================================================
     SECTION 5: GET STARTED + TRUST
     ================================================================ */
  .trust-section {
    padding: 40px 16px;
    background: var(--color-bg-secondary);
    border-top: 1px solid;
    border-image: linear-gradient(135deg, #00e5ff 0%, #7c3aed 50%, #a855f7 100%) 1;
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
  }

  @media (max-width: 640px) {
    .hero {
      padding: 52px 16px 24px;
    }

    .hero__headline {
      font-size: 22px;
    }

    .score-row {
      grid-template-columns: 60px 1fr 1fr 40px;
      gap: 4px;
    }
  }
</style>
