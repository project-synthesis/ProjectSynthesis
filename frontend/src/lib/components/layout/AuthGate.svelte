<script lang="ts">
  import { workbench } from '$lib/stores/workbench.svelte';
  import { getGitHubLoginUrl } from '$lib/api/client';

  // ── Clipboard state ───────────────────────────────────────────────────────
  let copied = $state<string | null>(null);

  async function copy(text: string, id: string) {
    try {
      await navigator.clipboard.writeText(text);
      copied = id;
      setTimeout(() => { copied = null; }, 1800);
    } catch { /* clipboard unavailable — silent fail */ }
  }

  function handleOAuth() {
    window.location.href = getGitHubLoginUrl();
  }

  const ENV_VARS = [
    { key: 'GITHUB_APP_CLIENT_ID',     hint: 'Iv1.xxxxxxxxxx', id: 'cid'  },
    { key: 'GITHUB_APP_CLIENT_SECRET', hint: 'xxxxxxxxxxxxxxxx', id: 'sec' },
  ] as const;
</script>

<!-- ── Full-screen centred card ──────────────────────────────────────────── -->
<div class="h-screen w-screen flex items-center justify-center bg-bg-primary px-4">
  <div
    class="auth-card bg-bg-card border border-border-subtle w-full"
    style="max-width: 440px;"
    data-testid="auth-gate"
  >

    <!-- ── Header (always visible) ──────────────────────────────────────── -->
    <div class="px-8 pt-8 pb-6">
      <h1
        class="font-display text-lg tracking-[0.18em] uppercase mb-1.5 leading-none"
        style="background: linear-gradient(135deg, #00e5ff 0%, #7c3aed 60%, #a855f7 100%);
               background-clip: text; -webkit-background-clip: text; color: transparent;"
      >
        PROMPTFORGE
      </h1>
      <p class="font-mono text-[10px] text-text-dim tracking-[0.05em]">
        AI-Powered Prompt Optimization
      </p>
    </div>

    <!-- ── Divider ────────────────────────────────────────────────────────── -->
    <div class="border-t border-border-subtle"></div>

    {#if workbench.githubOAuthEnabled}

      <!-- ══════════════════════════════════════════════════════════════════
           CONFIGURED STATE — clean OAuth login
           ══════════════════════════════════════════════════════════════════ -->
      <div class="px-8 py-7" data-testid="auth-gate-login">

        <p class="font-mono text-[9px] text-text-dim uppercase tracking-[0.14em] mb-5">
          Sign in to continue
        </p>

        <button
          class="w-full flex items-center justify-center gap-2.5 px-4 py-2.5
            bg-neon-cyan text-bg-primary border border-neon-cyan
            hover:bg-[#00cce6] active:bg-[#00b8cf]
            transition-colors duration-150
            font-mono text-[11px] tracking-[0.07em] uppercase"
          onclick={handleOAuth}
          data-testid="auth-gate-oauth"
        >
          <!-- GitHub Invertocat mark -->
          <svg class="w-[15px] h-[15px] shrink-0" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
          </svg>
          Continue with GitHub
        </button>

      </div>

    {:else}

      <!-- ══════════════════════════════════════════════════════════════════
           UNCONFIGURED STATE — guided dev setup
           ══════════════════════════════════════════════════════════════════ -->
      <div class="px-8 py-6" data-testid="auth-gate-unconfigured">

        <!-- Status badge -->
        <div class="flex items-center gap-2.5 mb-5">
          <span class="font-mono text-[8px] uppercase tracking-[0.14em] text-neon-red border border-neon-red/35 px-1.5 py-[3px] leading-none">
            SETUP REQUIRED
          </span>
          <span class="font-mono text-[9px] text-text-dim">GitHub App not configured</span>
        </div>

        <!-- ── Steps ──────────────────────────────────────────────────── -->
        <div class="space-y-0">

          <!-- Step 01 -->
          <div class="step-row">
            <span class="step-num">01</span>
            <div class="step-body">
              <div class="step-title">Create GitHub App</div>
              <a
                href="https://github.com/settings/apps/new"
                target="_blank"
                rel="noopener noreferrer"
                class="font-mono text-[9px] text-neon-cyan/70 hover:text-neon-cyan
                  transition-colors duration-150 inline-flex items-center gap-1 mt-0.5 mb-2"
              >
                <svg class="w-2.5 h-2.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
                </svg>
                github.com/settings/apps/new
              </a>
              <!-- Required callback value -->
              <div class="bg-bg-input border border-border-subtle px-2.5 py-1.5">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-[8px] text-text-dim/60 uppercase tracking-[0.08em] shrink-0">Callback URL</span>
                  <span class="font-mono text-[8px] text-text-secondary flex-1 truncate">
                    http://localhost:8000/auth/github/callback
                  </span>
                  <button
                    class="copy-pill {copied === 'cb' ? 'copied' : ''}"
                    onclick={() => copy('http://localhost:8000/auth/github/callback', 'cb')}
                    title="Copy callback URL"
                  >
                    {copied === 'cb' ? '✓' : 'COPY'}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div class="step-divider"></div>

          <!-- Step 02 -->
          <div class="step-row">
            <span class="step-num">02</span>
            <div class="step-body">
              <div class="step-title">Add to <code class="font-mono text-[9px] text-text-secondary">.env</code></div>
              <div class="bg-bg-input border border-border-subtle px-2.5 py-2 mt-2 space-y-1.5">
                {#each ENV_VARS as v}
                  <div class="flex items-center gap-1.5 min-w-0">
                    <!-- Key -->
                    <span class="font-mono text-[8.5px] text-neon-green shrink-0">{v.key}</span>
                    <span class="font-mono text-[8px] text-text-dim shrink-0">=</span>
                    <!-- Placeholder -->
                    <span class="font-mono text-[8px] text-text-dim/40 flex-1 truncate">{v.hint}</span>
                    <!-- Copy button -->
                    <button
                      class="copy-pill {copied === v.id ? 'copied' : ''}"
                      onclick={() => copy(v.key, v.id)}
                      title="Copy {v.key}"
                    >
                      {copied === v.id ? '✓' : 'COPY'}
                    </button>
                  </div>
                {/each}
              </div>
            </div>
          </div>

          <div class="step-divider"></div>

          <!-- Step 03 -->
          <div class="step-row">
            <span class="step-num">03</span>
            <div class="step-body">
              <div class="step-title">Restart &amp; Reload</div>
              <div class="bg-bg-input border border-border-subtle px-2.5 py-1.5 mt-2">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-[8.5px] text-text-secondary flex-1">./init.sh restart</span>
                  <button
                    class="copy-pill {copied === 'cmd' ? 'copied' : ''}"
                    onclick={() => copy('./init.sh restart', 'cmd')}
                  >
                    {copied === 'cmd' ? '✓' : 'COPY'}
                  </button>
                </div>
              </div>
              <!-- Reload trigger -->
              <button
                class="w-full mt-2 flex items-center justify-center gap-2 px-3 py-2
                  border border-border-subtle text-text-dim
                  hover:border-neon-cyan/25 hover:text-text-secondary hover:bg-bg-hover
                  transition-colors duration-150
                  font-mono text-[9px] uppercase tracking-[0.1em]"
                onclick={() => window.location.reload()}
              >
                <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/>
                </svg>
                Reload Page
              </button>
            </div>
          </div>

        </div>

        <!-- Footer links -->
        <div class="mt-5 pt-4 border-t border-border-subtle flex items-center justify-between">
          <span class="font-mono text-[8px] text-text-dim/50 uppercase tracking-[0.1em]">
            zenresources.net
          </span>
          <a
            href="https://github.com/settings/apps"
            target="_blank"
            rel="noopener noreferrer"
            class="font-mono text-[8px] text-text-dim/50 hover:text-text-dim
              transition-colors duration-150 uppercase tracking-[0.1em]"
          >
            Manage Apps →
          </a>
        </div>

      </div>

    {/if}
  </div>
</div>

<style>
  /* ── Card entrance ──────────────────────────────────────────────────────── */
  .auth-card {
    animation: forge-enter 0.35s cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  @keyframes forge-enter {
    from { opacity: 0; transform: translateY(10px); }
    to   { opacity: 1; transform: translateY(0);    }
  }

  /* ── Step layout ────────────────────────────────────────────────────────── */
  .step-row {
    display: flex;
    gap: 12px;
    padding: 14px 0;
  }

  .step-num {
    font-family: var(--font-mono, ui-monospace);
    font-size: 10px;
    color: var(--color-neon-cyan, #00e5ff);
    flex-shrink: 0;
    padding-top: 1px;
    width: 18px;
    letter-spacing: 0.05em;
  }

  .step-body {
    flex: 1;
    min-width: 0;
  }

  .step-title {
    font-family: var(--font-display, ui-sans-serif);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary, #e4e4f0);
    margin-bottom: 6px;
  }

  .step-divider {
    height: 1px;
    background: var(--color-border-subtle, rgba(74, 74, 106, 0.15));
    margin-left: 30px;
  }

  /* ── Copy pill button ───────────────────────────────────────────────────── */
  .copy-pill {
    font-family: var(--font-mono, ui-monospace);
    font-size: 7px;
    letter-spacing: 0.1em;
    padding: 2px 6px;
    flex-shrink: 0;
    border: 1px solid var(--color-border-subtle, rgba(74, 74, 106, 0.15));
    color: var(--color-text-dim, #7a7a9e);
    background: transparent;
    cursor: pointer;
    transition: border-color 150ms, color 150ms;
    line-height: 1.4;
  }

  .copy-pill:hover {
    border-color: rgba(0, 229, 255, 0.3);
    color: var(--color-text-secondary, #8b8ba8);
  }

  .copy-pill.copied {
    border-color: rgba(34, 255, 136, 0.35);
    color: var(--color-neon-green, #22ff88);
  }

  /* ── Respect reduced motion ─────────────────────────────────────────────── */
  @media (prefers-reduced-motion: reduce) {
    .auth-card { animation-duration: 0.01ms; }
  }
</style>
