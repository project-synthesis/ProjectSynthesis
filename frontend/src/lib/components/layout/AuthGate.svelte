<script lang="ts">
  import { workbench } from '$lib/stores/workbench.svelte';
  import { getGitHubLoginUrl, submitGitHubPAT } from '$lib/api/client';

  let pat = $state('');
  let loading = $state(false);
  let error = $state('');

  async function handlePATSubmit(e: Event) {
    e.preventDefault();
    if (!pat.trim()) return;
    loading = true;
    error = '';
    try {
      await submitGitHubPAT(pat.trim());
      // auth.setToken() is called inside submitGitHubPAT on success —
      // auth.isAuthenticated becomes true → layout re-renders to workbench.
    } catch (err) {
      error = err instanceof Error ? err.message : 'Authentication failed';
    } finally {
      loading = false;
    }
  }

  function handleOAuth() {
    window.location.href = getGitHubLoginUrl();
  }
</script>

<div class="h-screen w-screen flex items-center justify-center bg-bg-primary">
  <div
    class="bg-bg-card border border-border-subtle rounded-none max-w-[360px] w-full p-8"
    data-testid="auth-gate"
  >
    <!-- Wordmark -->
    <h1
      class="font-display text-lg tracking-[0.15em] uppercase mb-1"
      style="background: linear-gradient(135deg, #00e5ff, #a855f7); background-clip: text; -webkit-background-clip: text; color: transparent;"
    >
      PROMPTFORGE
    </h1>

    <!-- Tagline -->
    <p class="font-mono text-[10px] text-text-dim mb-5 tracking-[0.05em]">
      AI-Powered Prompt Optimization
    </p>

    <div class="border-t border-border-subtle mb-5"></div>

    <!-- GitHub OAuth button (conditional) -->
    {#if workbench.githubOAuthEnabled}
      <button
        class="w-full flex items-center justify-center gap-2 px-4 py-2 mb-4
          border border-neon-cyan text-bg-primary bg-neon-cyan
          hover:bg-neon-cyan/90 transition-colors duration-200
          font-mono text-[11px] tracking-[0.05em] rounded-none"
        onclick={handleOAuth}
        data-testid="auth-gate-oauth"
      >
        <!-- GitHub mark SVG -->
        <svg class="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
        </svg>
        Continue with GitHub
      </button>

      <!-- Separator -->
      <div class="flex items-center gap-3 mb-4">
        <div class="flex-1 border-t border-border-subtle"></div>
        <span class="font-mono text-[10px] text-text-dim/50">— or —</span>
        <div class="flex-1 border-t border-border-subtle"></div>
      </div>
    {/if}

    <!-- PAT section -->
    <form onsubmit={handlePATSubmit}>
      <label for="auth-gate-pat" class="block font-mono text-[10px] text-text-dim uppercase tracking-[0.1em] mb-2">
        Personal Access Token
      </label>

      <input
        id="auth-gate-pat"
        type="password"
        bind:value={pat}
        placeholder="ghp_..."
        disabled={loading}
        class="w-full bg-bg-input border border-border-subtle text-text-primary
          focus:border-neon-cyan/40 focus:outline-none
          font-mono text-[13px] px-3 py-2 mb-3 rounded-none
          placeholder:text-text-dim/40"
        data-testid="auth-gate-pat-input"
      />

      <button
        type="submit"
        disabled={loading || !pat.trim()}
        class="w-full px-4 py-2
          border border-neon-cyan/30 text-neon-cyan
          hover:border-neon-cyan/60 hover:bg-neon-cyan/5
          transition-colors duration-200
          font-mono text-[11px] tracking-[0.05em] rounded-none
          disabled:opacity-40 disabled:cursor-not-allowed"
        data-testid="auth-gate-pat-submit"
      >
        {loading ? 'Authenticating...' : 'Authenticate with PAT'}
      </button>
    </form>

    <!-- Error display -->
    {#if error}
      <p class="mt-3 font-mono text-[10px] text-neon-red/80" data-testid="auth-gate-error">
        {error}
      </p>
    {/if}
  </div>
</div>
