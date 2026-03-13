<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { replaceState } from '$app/navigation';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { fetchHealth, fetchGitHubAuthStatus, fetchGitHubRepos, fetchLinkedRepo, fetchOptimization, fetchAuthMe, unlinkRepo, trackOnboardingEvent, notifyAuthReady, fetchHistoryStats } from '$lib/api/client';
  import { toast } from '$lib/stores/toast.svelte';
  import type { RepoInfo } from '$lib/api/client';
  import { user } from '$lib/stores/user.svelte';
  import { auth } from '$lib/stores/auth.svelte';
  import { history } from '$lib/stores/history.svelte';
  import { commandPalette } from '$lib/stores/commandPalette.svelte';
  import ActivityBar from '$lib/components/layout/ActivityBar.svelte';
  import Navigator from '$lib/components/layout/Navigator.svelte';
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import Inspector from '$lib/components/layout/Inspector.svelte';
  import StatusBar from '$lib/components/layout/StatusBar.svelte';
  import AuthGate from '$lib/components/layout/AuthGate.svelte';
  import OnboardingModal from '$lib/components/layout/OnboardingModal.svelte';
  import CommandPalette from '$lib/components/shared/CommandPalette.svelte';
  import ToastContainer from '$lib/components/shared/ToastContainer.svelte';
  import SpotlightOverlay from '$lib/components/shared/SpotlightOverlay.svelte';
  import { walkthrough } from '$lib/stores/walkthrough.svelte';

  import type { Snippet } from 'svelte';
  let { children }: { children: Snippet } = $props();

  // Non-reactive re-entry guards — plain `let` (not $state) so they don't
  // trigger reactive effects when set and reset correctly on HMR/remount.
  let _profileFetching = false;
  let _githubFetching = false;

  // Tab-switch forge state restoration
  $effect(() => {
    const tab = editor.activeTab;
    if (forge.isForging) return;

    if (tab?.optimizationId) {
      // Skip if forge already shows this optimization's data (avoids double-load
      // when navigators pre-call loadFromRecord before openTab triggers this effect)
      if (forge.optimizationId === tab.optimizationId) return;

      const cached = forge.getRecord(tab.optimizationId);
      if (cached) {
        forge.loadFromRecord(cached);
      } else {
        // Capture the requested ID so the async callback can detect stale results
        // (user may switch tabs before the fetch resolves)
        const requestedId = tab.optimizationId;
        fetchOptimization(requestedId)
          .then(record => {
            forge.cacheRecord(record.id, record);
            // Only apply if the active tab still wants this record
            if (editor.activeTab?.optimizationId === record.id) {
              forge.loadFromRecord(record);
            }
          })
          .catch(() => {
            // Only reset if still on the same tab that triggered the fetch
            if (editor.activeTab?.optimizationId === requestedId) {
              forge.resetPipeline();
            }
          });
      }
    } else {
      forge.resetPipeline();
    }
  });

  // Hydrate GitHub state whenever the user becomes authenticated.
  // Runs on first auth (OAuth) and on page reload with a valid refresh cookie.
  // Fires reactively so a freshly completed OAuth flow is immediately reflected without a hard refresh.
  $effect(() => {
    if (!auth.isAuthenticated) { _githubFetching = false; return; }
    // Re-entry guard: token rotations re-trigger this effect; skip if already in flight.
    if (_githubFetching) return;
    _githubFetching = true;
    fetchGitHubAuthStatus()
      .then(async (authStatus) => {
        if (authStatus.connected && authStatus.login) {
          try {
            const repos = await fetchGitHubRepos();
            github.setConnected(
              authStatus.login,
              repos.map((r: RepoInfo) => ({
                full_name:      r.full_name,
                description:    r.description ?? '',
                default_branch: r.default_branch ?? 'main',
                private:        !!r.private,
                language:       r.language ?? undefined,
                size_kb:        r.size_kb,
                stars:          r.stars,
                forks:          r.forks,
                open_issues:    r.open_issues,
                updated_at:     r.updated_at ?? undefined,
                pushed_at:      r.pushed_at ?? undefined,
                license_name:   r.license_name ?? undefined,
                topics:         r.topics,
              }))
            );
            // Restore linked repo selection
            const linked = await fetchLinkedRepo();
            if (linked && linked.full_name) {
              github.selectRepo(linked.full_name, linked.branch);
            }
          } catch (repoErr) {
            // Repos fetch failed — mark connected but without repos
            github.setConnected(authStatus.login, []);
            github.reposFetchError = (repoErr as Error).message || 'Failed to fetch repositories';
          }
        }
      })
      .catch(() => {
        // Not connected or auth check failed — leave github store in default state
      })
      .finally(() => { _githubFetching = false; });
  });

  // Eagerly fetch history stats so WelcomeTab checklist "First synthesis complete"
  // persists across page reloads without requiring the History panel to mount.
  let _historyStatsFetching = false;
  $effect(() => {
    if (!auth.isAuthenticated) { _historyStatsFetching = false; return; }
    if (_historyStatsFetching) return;
    _historyStatsFetching = true;
    fetchHistoryStats()
      .then(stats => { history.totalCount = stats.total_optimizations; })
      .catch(() => { /* API not ready yet — checklist stays unchecked until health reconnects */ })
      .finally(() => { _historyStatsFetching = false; });
  });

  // Refresh history and sync editor tab after forge completion (normal or retry).
  $effect(() => {
    const seq = forge.completionSeq;
    if (seq === 0) return; // skip initial mount

    // Sync active tab's optimizationId when a retry produces a new record.
    const tab = editor.activeTab;
    if (tab && forge.optimizationId && tab.optimizationId && tab.optimizationId !== forge.optimizationId) {
      tab.optimizationId = forge.optimizationId;
    }

    // Refresh history list so NavigatorHistory shows the new entry.
    history.loadHistory();

    // Refresh stats (total count, avg score, framework breakdown)
    fetchHistoryStats()
      .then(s => { history.totalCount = s.total_optimizations; })
      .catch(() => {});
  });

  // Hydrate User profile (display_name, avatar_url, email) when authenticated.
  $effect(() => {
    if (!auth.isAuthenticated) { user.clearProfile(); _profileFetching = false; return; }
    // Re-entry guard: token rotations re-trigger this effect; skip if already in flight.
    if (_profileFetching) return;
    _profileFetching = true;
    user.loading = true;
    fetchAuthMe()
      .then(p => user.setProfile(p))
      .catch(e => { user.error = (e as Error).message; user.profileFetchFailed = true; })
      .finally(() => { user.loading = false; _profileFetching = false; });
  });

  // Auto-resume onboarding wizard for users who haven't completed it.
  // Only triggers on return visits (not initial auth callback — that's handled in onMount).
  // F4: Skip if profile fetch failed — avoids re-showing wizard to returning users.
  // F2: Also trigger when backend says not completed (not just localStorage).
  $effect(() => {
    if (
      authChecked
      && auth.isAuthenticated
      && !user.onboardingCompleted
      && !user.profileFetchFailed
      && !user.loading  // Wait for profile hydration
      && !workbench.showOnboarding  // Don't double-trigger
    ) {
      // Check if there's a persisted wizard step (user was mid-wizard)
      const storedStep = typeof window !== 'undefined' ? localStorage.getItem('pf_onboarding_step') : null;
      if (storedStep) {
        workbench.showOnboarding = true;
      } else if (!user.preferences.dismissedTips.includes('wizard-auto-dismissed')) {
        // F2: Backend says not completed and user hasn't explicitly dismissed — show wizard
        workbench.showOnboarding = true;
      }
    }
  });

  // Auth gate — false until the silent refresh attempt resolves
  let authChecked = $state(false);

  // Auto-dismiss welcome-back banner after 10s
  let showWelcomeBack = $derived(
    authChecked && auth.isAuthenticated && user.isNewUser
    && !workbench.showOnboarding
    && !user.loading  // Wait for profile to load before showing
    && !user.preferences.dismissedTips.includes('welcome-back-banner')
  );
  $effect(() => {
    if (showWelcomeBack) {
      const timer = setTimeout(() => user.dismissTip('welcome-back-banner'), 10_000);
      return () => clearTimeout(timer);
    }
  });

  // Resize handle logic
  let resizing = $state<'nav' | 'inspector' | null>(null);
  let startX = 0;
  let startWidth = 0;

  function startNavResize(e: MouseEvent) {
    if (workbench.navigatorCollapsed) return;
    resizing = 'nav';
    startX = e.clientX;
    startWidth = workbench.navigatorWidth;
    e.preventDefault();
  }

  function startInspectorResize(e: MouseEvent) {
    if (workbench.inspectorCollapsed) return;
    resizing = 'inspector';
    startX = e.clientX;
    startWidth = workbench.inspectorWidth;
    e.preventDefault();
  }

  function handleMouseMove(e: MouseEvent) {
    if (!resizing) return;
    if (resizing === 'nav') {
      const delta = e.clientX - startX;
      workbench.setNavigatorWidth(startWidth + delta);
    } else if (resizing === 'inspector') {
      const delta = startX - e.clientX;
      workbench.setInspectorWidth(startWidth + delta);
    }
  }

  function handleMouseUp() {
    resizing = null;
  }

  // Responsive auto-collapse:
  // <768px: mobile — both panels collapse
  // >=768px: desktop — panels stay at user-set state
  function handleResize() {
    const w = window.innerWidth;
    if (w < 768) {
      workbench.setNavigatorCollapsed(true);
      workbench.setInspectorCollapsed(true);
    }
  }

  // Global keyboard shortcuts:
  //   Alt+↑/↓         — cycle through open tabs
  //   Alt+←/→         — focus Navigator / Inspector panel
  //   Alt+1–8         — switch to Nth open tab
  //   Alt+N           — new prompt (via command palette)
  //   Alt+W           — close tab (via command palette)
  //   Ctrl+Shift+E/H/L/Y/G — switch Activity Bar panel
  //   Ctrl+S/,/K/B/I  — actions (via command palette)
  //   Escape          — cancel active forge
  function handleKeyboard(e: KeyboardEvent) {
    // Alt+↑/↓ — tab cycling
    if (e.altKey && !e.ctrlKey && !e.shiftKey) {
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        const tabs = editor.openTabs;
        if (tabs.length > 1) {
          const idx = tabs.findIndex(t => t.id === editor.activeTabId);
          const next = e.key === 'ArrowUp'
            ? (idx - 1 + tabs.length) % tabs.length
            : (idx + 1) % tabs.length;
          editor.activeTabId = tabs[next].id;
        }
        return;
      }
      // Alt+← — focus Navigator panel
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (workbench.navigatorCollapsed) workbench.setNavigatorCollapsed(false);
        const nav = document.querySelector('nav[aria-label="Navigator"]') as HTMLElement;
        if (nav) {
          const focusable = nav.querySelector<HTMLElement>('button, a, input, select, textarea, [tabindex="0"]');
          (focusable ?? nav).focus();
        }
        return;
      }
      // Alt+→ — focus Inspector panel
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (workbench.inspectorCollapsed) workbench.setInspectorCollapsed(false);
        const inspector = document.querySelector('aside[aria-label="Inspector"]') as HTMLElement;
        if (inspector) {
          const focusable = inspector.querySelector<HTMLElement>('button, a, input, select, textarea, [tabindex="0"]');
          (focusable ?? inspector).focus();
        }
        return;
      }
      // Alt+1–8: switch to Nth open tab
      const num = parseInt(e.key);
      if (num >= 1 && num <= 8) {
        e.preventDefault();
        const tab = editor.openTabs[num - 1];
        if (tab) editor.activeTabId = tab.id;
        return;
      }
    }
    // Escape — cancel active forge
    if (e.key === 'Escape' && forge.isForging) {
      forge.cancel();
      return;
    }
    // Ctrl+Shift+* — Activity Bar panel shortcuts
    if (e.ctrlKey && e.shiftKey) {
      if (e.key === 'E') { e.preventDefault(); workbench.setActivity('files'); }
      else if (e.key === 'H') { e.preventDefault(); workbench.setActivity('history'); }
      else if (e.key === 'L') { e.preventDefault(); workbench.setActivity('chains'); }
      else if (e.key === 'Y') { e.preventDefault(); workbench.setActivity('templates'); }
      else if (e.key === 'G') { e.preventDefault(); workbench.setActivity('github'); }
    }
  }

  onMount(() => {
    // Reset auth gate — ensures workbench never renders with a stale HMR-preserved
    // authChecked=true value before the silent refresh attempt completes.
    authChecked = false;

    // ── JWT token capture ──────────────────────────────────────────────
    // After GitHub OAuth redirect the backend sends the user to /?auth_complete=1.
    // We exchange the one-time server-side session token via GET /auth/token —
    // the JWT never appears in the redirect URL (ASVS §3.5.2).
    (async () => {
      const url = new URL(window.location.href);
      const isAuthCallback = url.searchParams.has('auth_complete');
      if (isAuthCallback) {
        const isNewUser = url.searchParams.has('new');
        try {
          const res = await fetch('/auth/token', { credentials: 'include' });
          if (res.ok) {
            const data: { access_token: string } = await res.json();
            auth.setToken(data.access_token);
            trackOnboardingEvent('auth_completed', { is_new: isNewUser });
            if (isNewUser) {
              workbench.showOnboarding = true;
            }
          }
        } catch { /* token fetch failed — fall through to silent refresh */ }
        // Clean up URL after callback
        url.searchParams.delete('auth_complete');
        url.searchParams.delete('new');
        replaceState(url.toString(), {});
        authChecked = true;
        if (auth.isAuthenticated) notifyAuthReady();
      } else {
        // Attempt silent refresh from httponly cookie (restores returning sessions)
        auth.refresh().finally(() => {
          authChecked = true;
          if (auth.isAuthenticated) notifyAuthReady();
        });
      }
    })();

    // Initial responsive check
    handleResize();
    window.addEventListener('resize', handleResize);
    document.addEventListener('keydown', handleKeyboard);

    // Health polling — runs immediately, then every 15 s.
    // Updates backend connection, MCP status, provider, and OAuth flag in one shot.
    let healthTimer: ReturnType<typeof setInterval>;

    async function pollHealth() {
      try {
        const data = await fetchHealth();
        workbench.isConnected = true;
        workbench.mcpConnected = !!data.mcp_connected;
        workbench.redisConnected = !!data.redis_connected;
        workbench.provider = (data.provider as 'claude_cli' | 'anthropic_api') || 'unknown';
        workbench.providerModel = data.model_routing?.optimize || '';
        workbench.githubOAuthEnabled = !!data.github_oauth_enabled;
        workbench.appVersion = data.version || '';
      } catch {
        workbench.isConnected = false;
        workbench.mcpConnected = false;
        workbench.redisConnected = false;
      }
    }

    pollHealth();
    healthTimer = setInterval(pollHealth, 15_000);

    // GitHub hydration moved to $effect below — runs reactively when auth.isAuthenticated becomes true

    // Ensure at least one tab is open
    editor.ensureWelcomeTab();

    // ── Command palette — single source of truth ─────────────────────────
    // Sections: File → View → Forge → History → GitHub (insertion order).
    // CommandPalette.svelte only renders; it never registers commands.

    // FILE — tab lifecycle
    commandPalette.registerCommand({
      id: 'new-prompt',
      label: 'New Prompt',
      shortcut: 'Alt+N',
      group: 'File',
      action: () => {
        editor.openTab({
          id: `prompt-${Date.now()}`,
          label: 'New Prompt',
          type: 'prompt',
          promptText: '',
          dirty: false
        });
      }
    });
    commandPalette.registerCommand({
      id: 'save-prompt',
      label: 'Save Prompt',
      shortcut: 'Ctrl+S',
      group: 'File',
      action: () => editor.saveActiveTab()
    });
    commandPalette.registerCommand({
      id: 'close-tab',
      label: 'Close Tab',
      shortcut: 'Alt+W',
      group: 'File',
      action: () => {
        if (editor.activeTabId) editor.closeTab(editor.activeTabId);
      }
    });

    // VIEW — panel / layout toggles
    commandPalette.registerCommand({
      id: 'toggle-navigator',
      label: 'Toggle Navigator',
      shortcut: 'Ctrl+B',
      group: 'View',
      action: () => workbench.toggleNavigator()
    });
    commandPalette.registerCommand({
      id: 'toggle-inspector',
      label: 'Toggle Inspector',
      shortcut: 'Ctrl+I',
      group: 'View',
      action: () => workbench.toggleInspector()
    });
    commandPalette.registerCommand({
      id: 'open-settings',
      label: 'Open Settings',
      shortcut: 'Ctrl+,',
      group: 'View',
      action: () => workbench.setActivity('settings')
    });

    // FORGE — optimization workflow
    commandPalette.registerCommand({
      id: 'forge.run',
      label: 'Run Optimization',
      shortcut: 'Ctrl+Enter',
      group: 'Forge',
      action: () => {
        setTimeout(() => {
          document.querySelector<HTMLButtonElement>('[data-testid="forge-button"]')?.click();
        }, 0);
      }
    });
    commandPalette.registerCommand({
      id: 'forge.new',
      label: 'New Optimization',
      description: 'Clear prompt and start fresh',
      group: 'Forge',
      action: () => {
        forge.resetPipeline();
        editor.openTab({
          id: `prompt-${Date.now()}`,
          label: 'New Prompt',
          type: 'prompt',
          promptText: '',
          dirty: false
        });
        setTimeout(() => {
          document.querySelector<HTMLTextAreaElement>('[data-testid="prompt-textarea"]')?.focus();
        }, 0);
      }
    });
    commandPalette.registerCommand({
      id: 'forge.retry',
      label: 'Retry Last Optimization',
      description: 'Re-run with same settings',
      group: 'Forge',
      action: () => {
        if (forge.optimizationId) {
          const rawPrompt = forge.rawPrompt;
          forge.retryForge(forge.optimizationId, undefined, rawPrompt);
        }
      }
    });

    // HISTORY — optimization history
    commandPalette.registerCommand({
      id: 'history.open',
      label: 'Open History',
      group: 'History',
      action: () => workbench.setActivity('history')
    });
    commandPalette.registerCommand({
      id: 'history.trash',
      label: 'Open Trash',
      group: 'History',
      action: () => {
        workbench.setActivity('history');
        history.showTrash = true;
        history.loadTrash();
      }
    });

    // GITHUB — repository management
    commandPalette.registerCommand({
      id: 'github.connect',
      label: 'Connect Repository',
      description: 'Pick a GitHub repo for context',
      group: 'GitHub',
      action: () => {
        workbench.setActivity('github');
        github.showRepoPicker = true;
      }
    });
    commandPalette.registerCommand({
      id: 'github.disconnect',
      label: 'Disconnect Repository',
      group: 'GitHub',
      action: async () => {
        if (github.isConnected) {
          try {
            await unlinkRepo();
          } catch { /* best-effort */ }
          github.disconnect();
        }
      }
    });

    // HELP — onboarding and reference
    commandPalette.registerCommand({
      id: 'help.welcome',
      label: 'Show Welcome Guide',
      group: 'Help',
      action: () => { workbench.showOnboarding = true; }
    });
    commandPalette.registerCommand({
      id: 'help.walkthrough',
      label: 'Interactive Walkthrough',
      group: 'Help',
      action: () => walkthrough.start()
    });
    commandPalette.registerCommand({
      id: 'help.shortcuts',
      label: 'Keyboard Shortcuts',
      group: 'Help',
      action: () => {
        editor.openTab({ id: 'welcome', label: 'Welcome', type: 'prompt', promptText: '', dirty: false });
      }
    });
    commandPalette.registerCommand({
      id: 'help.sample',
      label: 'Load Sample Prompt',
      group: 'Help',
      action: () => workbench.setActivity('templates')
    });
    commandPalette.registerCommand({
      id: 'help.strategies',
      label: 'Strategy Reference',
      group: 'Help',
      action: () => {
        editor.openTab({ id: 'strategy-ref', label: 'Strategies', type: 'strategy-ref', dirty: false });
      }
    });

    return () => {
      clearInterval(healthTimer);
      window.removeEventListener('resize', handleResize);
      document.removeEventListener('keydown', handleKeyboard);
    };
  });
</script>

{#if !authChecked}
  <!-- Loading: centered wordmark while silent refresh resolves -->
  <div class="h-screen w-screen flex items-center justify-center bg-bg-primary">
    <span class="font-display text-sm tracking-[0.2em] uppercase text-text-dim/50">
      PROJECT SYNTHESIS
    </span>
  </div>
{:else if !auth.isAuthenticated}
  <AuthGate />
{:else}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="h-screen w-screen overflow-hidden grid"
    style="
      grid-template-columns: 40px {workbench.navCssWidth} 1fr {workbench.inspectorCssWidth};
      grid-template-rows: 1fr 24px;
      {resizing ? '' : 'transition: grid-template-columns 0.2s ease;'}
    "
    onmousemove={handleMouseMove}
    onmouseup={handleMouseUp}
    onmouseleave={handleMouseUp}
  >
    <!-- Row 1: Activity Bar -->
    <div class="row-span-1" style="grid-row: 1; grid-column: 1;">
      <ActivityBar />
    </div>

    <!-- Row 1: Navigator -->
    <div class="row-span-1 overflow-hidden relative" style="grid-row: 1; grid-column: 2;">
      <Navigator />
      <!-- Navigator resize handle (right edge) -->
      {#if !workbench.navigatorCollapsed}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-neon-cyan/30 transition-colors z-[100]
            {resizing === 'nav' ? 'bg-neon-cyan/40' : ''}"
          data-testid="nav-resize-handle"
          onmousedown={startNavResize}
        ></div>
      {/if}
    </div>

    <!-- Row 1: Editor (main) -->
    <div class="row-span-1 overflow-hidden" style="grid-row: 1; grid-column: 3;">
      <EditorGroups />
      <!-- Page slot (empty for workbench since layout handles everything) -->
      <div class="hidden">
        {@render children()}
      </div>
    </div>

    <!-- Row 1: Inspector -->
    <div class="row-span-1 overflow-hidden relative" style="grid-row: 1; grid-column: 4;">
      <!-- Inspector resize handle (left edge) -->
      {#if !workbench.inspectorCollapsed}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-neon-cyan/30 transition-colors z-[100]
            {resizing === 'inspector' ? 'bg-neon-cyan/40' : ''}"
          data-testid="inspector-resize-handle"
          onmousedown={startInspectorResize}
        ></div>
      {/if}
      <Inspector />
    </div>

    <!-- Row 2: Status Bar spans full width -->
    <div style="grid-row: 2; grid-column: 1 / -1;" data-tour="statusbar">
      <StatusBar />
    </div>
  </div>

  <!-- Welcome-back banner for returning users who haven't completed setup -->
  {#if showWelcomeBack}
    <div class="fixed top-0 left-0 right-0 z-40 bg-bg-card border-b border-neon-cyan/20 px-4 py-2 flex items-center justify-between animate-fade-in">
      <span class="font-mono text-[10px] text-text-dim">
        Welcome back — you haven't completed setup yet.
      </span>
      <div class="flex items-center gap-2">
        <button
          onclick={() => editor.openTab({ id: 'welcome', label: 'Welcome', type: 'prompt', promptText: '', dirty: false })}
          class="font-mono text-[10px] text-neon-cyan hover:text-neon-cyan/80"
        >OPEN GUIDE</button>
        <button
          onclick={() => user.dismissTip('welcome-back-banner')}
          class="font-mono text-[10px] text-text-dim/40 hover:text-text-dim"
        >DISMISS</button>
      </div>
    </div>
  {/if}

  <!-- Session expiry warning -->
  {#if auth.tokenExpiringSoon}
    <div class="fixed top-0 left-0 right-0 z-50 bg-bg-card border-b border-neon-yellow/30 px-4 py-2 flex items-center justify-between animate-fade-in">
      <span class="font-mono text-[10px] text-neon-yellow">
        Session expiring soon — refreshing...
      </span>
      <button
        onclick={() => { auth.refresh(); }}
        class="font-mono text-[10px] text-neon-cyan hover:text-neon-cyan/80"
      >REFRESH NOW</button>
    </div>
  {/if}

  <!-- Global overlays -->
  <CommandPalette />
  <ToastContainer />
  {#if walkthrough.active}
    <SpotlightOverlay />
  {/if}
  {#if workbench.showOnboarding}
    <OnboardingModal onComplete={() => { workbench.showOnboarding = false; }} githubConnected={github.isConnected} repoLinked={github.selectedRepo != null} />
  {/if}
{/if}
