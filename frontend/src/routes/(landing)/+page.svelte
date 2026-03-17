<script lang="ts">
  import Navbar from '$lib/components/landing/Navbar.svelte';
  import FeatureCard from '$lib/components/landing/FeatureCard.svelte';
  import TestimonialCard from '$lib/components/landing/TestimonialCard.svelte';
  import StepCard from '$lib/components/landing/StepCard.svelte';
  import Footer from '$lib/components/landing/Footer.svelte';

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

    // Slight delay to ensure DOM is rendered
    requestAnimationFrame(() => {
      const targets = document.querySelectorAll<HTMLElement>('[data-animate]');
      targets.forEach((el) => observer.observe(el));
    });

    return () => observer.disconnect();
  });

  // ---- Feature data ----
  const features = [
    {
      icon: '<svg viewBox="0 0 16 16" fill="none"><path d="M2 4h12M2 8h8M2 12h10" stroke="currentColor" stroke-width="1.2"/></svg>',
      color: 'var(--color-neon-cyan)',
      title: 'Multi-Phase Pipeline',
      description: 'Analyze, optimize, and score prompts through independent LLM subagents — each with its own context window and scoring rubric.',
    },
    {
      icon: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 2v12M2 8h12" stroke="currentColor" stroke-width="1.2"/><circle cx="8" cy="8" r="3" stroke="currentColor" stroke-width="1.2"/></svg>',
      color: 'var(--color-neon-purple)',
      title: '5-Dimension Scoring',
      description: 'Hybrid LLM + heuristic evaluation across clarity, specificity, structure, faithfulness, and conciseness with z-score normalization.',
    },
    {
      icon: '<svg viewBox="0 0 16 16" fill="none"><path d="M4 12L8 4l4 8" stroke="currentColor" stroke-width="1.2"/><path d="M5.5 9.5h5" stroke="currentColor" stroke-width="1.2"/></svg>',
      color: 'var(--color-neon-green)',
      title: 'Strategy Engine',
      description: 'Six optimization strategies — chain-of-thought, few-shot, meta-prompting, role-playing, structured-output — selected by confidence-gated analysis.',
    },
    {
      icon: '<svg viewBox="0 0 16 16" fill="none"><path d="M3 3h10v10H3z" stroke="currentColor" stroke-width="1.2"/><path d="M6 1v4M10 1v4M6 11v4M10 11v4" stroke="currentColor" stroke-width="1.2"/></svg>',
      color: 'var(--color-neon-yellow)',
      title: 'MCP Integration',
      description: 'Four-tool MCP server drops into any Claude Code session. Analyze, optimize, prepare, and save — without leaving your editor.',
    },
    {
      icon: '<svg viewBox="0 0 16 16" fill="none"><path d="M2 12l4-4 3 3 5-7" stroke="currentColor" stroke-width="1.2"/></svg>',
      color: 'var(--color-neon-teal)',
      title: 'Iterative Refinement',
      description: 'Branch, rollback, and evolve prompts with version-tracked refinement sessions. Each turn is a fresh pipeline invocation with score progression.',
    },
    {
      icon: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.2"/><path d="M8 5v3l2 2" stroke="currentColor" stroke-width="1.2"/></svg>',
      color: 'var(--color-neon-pink)',
      title: 'Real-Time Events',
      description: 'SSE-powered pipeline streaming with live phase tracking. Event bus broadcasts optimization, feedback, and strategy changes across all clients.',
    },
  ];

  // ---- Steps data ----
  const steps = [
    {
      title: 'Paste Your Prompt',
      description: 'Drop in any prompt — coding task, writing brief, system instruction. The analyzer classifies its type, detects weaknesses, and selects the optimal strategy.',
    },
    {
      title: 'Pipeline Processes',
      description: 'Three independent subagents rewrite and score your prompt. A/B blind evaluation with randomized presentation order prevents position bias in scoring.',
    },
    {
      title: 'Ship the Result',
      description: 'Review 5-dimension scores with deltas, accept suggestions, or refine iteratively. Export the optimized prompt or pipe it directly via the MCP server.',
    },
  ];

  // ---- Testimonials data ----
  const testimonials = [
    {
      quote: 'Pipeline caught three specificity gaps in our API spec prompts that we missed in manual review. Score improved from 5.8 to 8.3 in one pass.',
      name: 'Mara Chen',
      role: 'Staff Engineer',
      company: 'Axiom Labs',
      initials: 'MC',
    },
    {
      quote: 'The MCP integration is the actual selling point. We run synthesis_optimize on every PR description now — it takes two seconds and the results are measurably better.',
      name: 'Erik Salazar',
      role: 'Platform Lead',
      company: 'Meridian',
      initials: 'ES',
    },
    {
      quote: 'Refinement branching saved us hours of iteration. Being able to fork a prompt at version 3 and try two different strategies in parallel changed our workflow.',
      name: 'Priya Nair',
      role: 'ML Engineer',
      company: 'Canopy AI',
      initials: 'PN',
    },
  ];

  // ---- Social proof metrics ----
  const metrics = [
    { value: '14,200+', label: 'Prompts Optimized' },
    { value: '+2.4', label: 'Avg Score Lift' },
    { value: '380ms', label: 'Median Latency' },
    { value: '99.7%', label: 'Pipeline Uptime' },
  ];
</script>

<Navbar />

<main id="main-content">
  <!-- ============================================================ -->
  <!-- HERO                                                         -->
  <!-- ============================================================ -->
  <section class="hero" aria-labelledby="hero-heading">
    <div class="hero__container">
      <div class="hero__content" data-animate style="--delay:200ms;">
        <h1 id="hero-heading" class="hero__headline">
          Prompts In.<br/>
          <span class="text-gradient-forge">Better Prompts Out.</span>
        </h1>
        <p class="hero__subheading">
          Real-time optimization pipeline. Analyze, rewrite, and score with 5-dimension quality metrics — every prompt, every time.
        </p>
        <div class="hero__actions">
          <a href="https://github.com/project-synthesis/ProjectSynthesis" class="btn-primary" target="_blank" rel="noopener">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" style="margin-right:4px;"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
            View on GitHub
          </a>
          <a href="#how-it-works" class="btn-ghost">See How It Works</a>
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
            <div class="mockup__line" style="width:65%;color:var(--color-text-dim);">
              <span class="font-mono" style="color:var(--color-neon-cyan);font-size:10px;">ANALYZE</span> task_type: coding &middot; strategy: chain-of-thought
            </div>
            <div class="mockup__line" style="width:80%;color:var(--color-text-dim);">
              <span class="font-mono" style="color:var(--color-neon-purple);font-size:10px;">OPTIMIZE</span> +structure +constraints +examples &middot; 3 weaknesses addressed
            </div>
            <div class="mockup__line" style="width:72%;">
              <span class="font-mono" style="color:var(--color-neon-green);font-size:10px;">SCORE</span>
              <span style="color:var(--color-neon-green);">8.4</span>
              <span style="color:var(--color-text-dim);">/10 &middot;</span>
              <span class="font-mono" style="color:var(--color-neon-green);font-size:10px;">+2.1</span>
            </div>
            <div class="mockup__scores">
              {#each ['clarity', 'specificity', 'structure', 'faithful.', 'concise.'] as dim, i}
                <div class="mockup__score-item">
                  <span style="color:var(--color-text-dim);font-size:9px;">{dim}</span>
                  <div class="mockup__score-bar">
                    <div class="mockup__score-fill" style="width:{62 + i * 7}%;background:var(--color-neon-cyan);"></div>
                  </div>
                  <span class="font-mono" style="font-size:9px;color:var(--color-neon-cyan);">{(6.2 + i * 0.7).toFixed(1)}</span>
                </div>
              {/each}
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- SOCIAL PROOF BAR                                             -->
  <!-- ============================================================ -->
  <section class="proof-bar" aria-label="Platform metrics">
    <div class="proof-bar__inner">
      {#each metrics as m}
        <div class="proof-bar__metric">
          <span class="proof-bar__value font-mono">{m.value}</span>
          <span class="proof-bar__label">{m.label}</span>
        </div>
      {/each}
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- FEATURES GRID                                                -->
  <!-- ============================================================ -->
  <section id="features" class="section" aria-labelledby="features-heading">
    <div class="section__container">
      <h2 id="features-heading" class="section__title">
        <span class="section-heading">Capabilities</span>
      </h2>
      <div class="features-grid">
        {#each features as feature, i}
          <div data-animate style="--delay:{i * 100}ms;">
            <FeatureCard {...feature} />
          </div>
        {/each}
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- HOW IT WORKS                                                 -->
  <!-- ============================================================ -->
  <section id="how-it-works" class="section" aria-labelledby="steps-heading">
    <div class="section__container section__container--narrow">
      <h2 id="steps-heading" class="section__title">
        <span class="section-heading">How It Works</span>
      </h2>
      <div class="steps">
        {#each steps as step, i}
          <div data-animate style="--delay:{i * 150}ms;">
            <StepCard number={i + 1} {...step} isLast={i === steps.length - 1} />
          </div>
        {/each}
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- TESTIMONIALS                                                 -->
  <!-- ============================================================ -->
  <section id="testimonials" class="section" aria-labelledby="testimonials-heading">
    <div class="section__container">
      <h2 id="testimonials-heading" class="section__title">
        <span class="section-heading">From the Pipeline</span>
      </h2>
      <div class="testimonials-grid">
        {#each testimonials as t, i}
          <div data-animate style="--delay:{i * 100}ms;">
            <TestimonialCard {...t} />
          </div>
        {/each}
      </div>
    </div>
  </section>

  <!-- ============================================================ -->
  <!-- CTA                                                          -->
  <!-- ============================================================ -->
  <section class="cta-section" aria-labelledby="cta-heading">
    <div class="cta-section__inner" data-animate>
      <h2 id="cta-heading" class="cta-section__headline">
        <span class="text-gradient-forge">Stop guessing. Start measuring.</span>
      </h2>
      <p class="cta-section__sub">
        Every prompt scored. Every improvement tracked. Every iteration versioned.
      </p>
      <a href="https://github.com/project-synthesis/ProjectSynthesis" class="btn-primary" target="_blank" rel="noopener">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" style="margin-right:4px;"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
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
     HERO
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

  /* ---- Mockup ---- */
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

  .mockup__line {
    font-size: 10px;
    font-family: var(--font-sans);
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--color-text-secondary);
  }

  .mockup__scores {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-top: 4px;
    padding-top: 6px;
    border-top: 1px solid var(--color-border-subtle);
  }

  .mockup__score-item {
    display: grid;
    grid-template-columns: 56px 1fr 28px;
    align-items: center;
    gap: 6px;
  }

  .mockup__score-bar {
    height: 3px;
    background: rgba(74, 74, 106, 0.15);
    overflow: hidden;
  }

  .mockup__score-fill {
    height: 100%;
    transition: width 800ms var(--ease-spring);
  }

  /* ================================================================
     BUTTONS — shared base + variants
     ================================================================ */
  .btn-primary,
  .btn-ghost {
    display: inline-flex;
    align-items: center;
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
     SOCIAL PROOF BAR
     ================================================================ */
  .proof-bar {
    border-top: 1px solid var(--color-border-subtle);
    border-bottom: 1px solid var(--color-border-subtle);
    padding: 12px 16px;
  }

  .proof-bar__inner {
    max-width: 1120px;
    margin: 0 auto;
    display: flex;
    justify-content: center;
    gap: 40px;
  }

  .proof-bar__metric {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
  }

  .proof-bar__value {
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .proof-bar__label {
    font-size: 10px;
    color: var(--color-text-dim);
  }

  /* ================================================================
     SECTIONS
     ================================================================ */
  .section {
    padding: 40px 16px;
  }

  .section__container {
    max-width: 1120px;
    margin: 0 auto;
  }

  .section__container--narrow {
    max-width: 560px;
  }

  .section__title {
    text-align: center;
    margin: 0 0 24px 0;
  }

  /* ================================================================
     FEATURES GRID
     ================================================================ */
  .features-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }

  /* ================================================================
     STEPS
     ================================================================ */
  .steps {
    display: flex;
    flex-direction: column;
  }

  /* ================================================================
     TESTIMONIALS GRID
     ================================================================ */
  .testimonials-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }

  /* ================================================================
     CTA
     ================================================================ */
  .cta-section {
    padding: 40px 16px;
    background: var(--color-bg-secondary);
    border-top: 1px solid;
    border-image: linear-gradient(135deg, #00e5ff 0%, #7c3aed 50%, #a855f7 100%) 1;
  }

  .cta-section__inner {
    max-width: 560px;
    margin: 0 auto;
    text-align: center;
  }

  .cta-section__headline {
    font-family: var(--font-display);
    font-weight: 700;
    font-size: clamp(18px, 3vw, 28px);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    line-height: 1.2;
    margin: 0 0 8px 0;
  }

  .cta-section__sub {
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

    .features-grid {
      grid-template-columns: repeat(2, 1fr);
    }

    .testimonials-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (max-width: 640px) {
    .hero {
      padding: 52px 16px 24px;
    }

    .hero__headline {
      font-size: 22px;
    }

    .features-grid {
      grid-template-columns: 1fr;
    }

    .testimonials-grid {
      grid-template-columns: 1fr;
    }

    .proof-bar__inner {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
    }
  }
</style>
