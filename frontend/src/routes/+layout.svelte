<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { fetchHealth, fetchGitHubAuthStatus, fetchGitHubRepos, fetchLinkedRepo, fetchOptimization, fetchAuthMe, unlinkRepo } from '$lib/api/client';
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

  import type { Snippet } from 'svelte';
  let { children }: { children: Snippet } = $props();

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
    if (!auth.isAuthenticated) return;
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
          } catch {
            // Repos fetch failed — mark connected but without repos
            github.setConnected(authStatus.login, []);
          }
        }
      })
      .catch(() => {
        // Not connected or auth check failed — leave github store in default state
      });
  });

  // Hydrate User profile (display_name, avatar_url, email) when authenticated.
  $effect(() => {
    if (!auth.isAuthenticated) { user.clearProfile(); return; }
    user.loading = true;
    fetchAuthMe()
      .then(p => user.setProfile(p))
      .catch(e => { user.error = (e as Error).message; toast.error('Profile load failed'); })
      .finally(() => { user.loading = false; });
  });

  // Auth gate — false until the silent refresh attempt resolves
  let authChecked = $state(false);
  // Onboarding modal — shown once for brand-new users after OAuth
  let showOnboarding = $state(false);

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

  // F6 zone cycling per spec: cycle focus through Activity Bar, Navigator, Editor, Inspector, Status Bar
  const zoneSelectors = [
    'nav[aria-label="Activity Bar"]',
    'nav[aria-label="Navigator"]',
    'main[aria-label="Editor"]',
    'aside[aria-label="Inspector"]',
    'footer[aria-label="Status Bar"]'
  ];
  let currentZoneIndex = $state(-1);

  // Global keyboard shortcuts:
  //   Ctrl+Tab / Ctrl+Shift+Tab  — cycle through open tabs
  //   Ctrl+Shift+E/H/L/T/G       — switch Activity Bar panel
  //   Ctrl+,                     — open Settings panel
  //   Ctrl+W                     — close active tab
  //   Ctrl+S                     — save active tab
  //   Ctrl+1–8                   — switch to Nth open tab
  //   Escape                     — cancel active forge
  function handleKeyboard(e: KeyboardEvent) {
    if (e.ctrlKey && e.key === 'Tab') {
      e.preventDefault();
      const tabs = editor.openTabs;
      if (tabs.length > 1) {
        const idx = tabs.findIndex(t => t.id === editor.activeTabId);
        editor.activeTabId = tabs[e.shiftKey ? (idx - 1 + tabs.length) % tabs.length : (idx + 1) % tabs.length].id;
      }
    } else if (e.key === 'Escape' && forge.isForging) {
      forge.cancel();
    } else if (e.ctrlKey && e.shiftKey) {
      // Activity Bar shortcuts (Ctrl+Shift+*)
      if (e.key === 'E') { e.preventDefault(); workbench.setActivity('files'); }
      else if (e.key === 'H') { e.preventDefault(); workbench.setActivity('history'); }
      else if (e.key === 'L') { e.preventDefault(); workbench.setActivity('chains'); }
      else if (e.key === 'T') { e.preventDefault(); workbench.setActivity('templates'); }
      else if (e.key === 'G') { e.preventDefault(); workbench.setActivity('github'); }
    } else if (e.ctrlKey && !e.shiftKey && !e.altKey) {
      if (e.key === ',') {
        e.preventDefault();
        workbench.setActivity('settings');
      } else if (e.key === 'w') {
        e.preventDefault();
        if (editor.activeTabId) editor.closeTab(editor.activeTabId);
      } else if (e.key === 's') {
        e.preventDefault();
        editor.saveActiveTab();
      } else {
        // Ctrl+1–8: switch to Nth open tab (1-indexed)
        const num = parseInt(e.key);
        if (num >= 1 && num <= 8) {
          e.preventDefault();
          const tab = editor.openTabs[num - 1];
          if (tab) editor.activeTabId = tab.id;
        }
      }
    }
  }

  function handleF6(e: KeyboardEvent) {
    if (e.key === 'F6') {
      e.preventDefault();
      // Advance to next zone (wrap around)
      currentZoneIndex = (currentZoneIndex + 1) % zoneSelectors.length;
      const zone = document.querySelector(zoneSelectors[currentZoneIndex]) as HTMLElement;
      if (zone) {
        // Remove focus outline from previous zone
        document.querySelectorAll('[data-zone-focused]').forEach(el => {
          el.removeAttribute('data-zone-focused');
          (el as HTMLElement).style.outline = '';
        });
        // Focus the zone
        zone.setAttribute('tabindex', '-1');
        zone.focus();
        zone.setAttribute('data-zone-focused', 'true');
        zone.style.outline = '1px solid rgba(0, 229, 255, 0.3)';
        zone.style.outlineOffset = '-1px';
        // Try to focus the first focusable element inside the zone
        const firstFocusable = zone.querySelector<HTMLElement>('button, a, input, select, textarea, [tabindex="0"]');
        if (firstFocusable) firstFocusable.focus();
      }
    }
  }

  onMount(() => {
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
            if (isNewUser) {
              showOnboarding = true;
            }
          }
        } catch { /* token fetch failed — fall through to silent refresh */ }
        // Clean up URL after callback
        url.searchParams.delete('auth_complete');
        url.searchParams.delete('new');
        window.history.replaceState({}, '', url.toString());
        authChecked = true;
      } else {
        // Attempt silent refresh from httponly cookie (restores returning sessions)
        auth.refresh().finally(() => {
          authChecked = true;
        });
      }
    })();

    // Initial responsive check
    handleResize();
    window.addEventListener('resize', handleResize);
    document.addEventListener('keydown', handleF6);
    document.addEventListener('keydown', handleKeyboard);

    // Health polling — runs immediately, then every 15 s.
    // Updates backend connection, MCP status, provider, and OAuth flag in one shot.
    let healthTimer: ReturnType<typeof setInterval>;

    async function pollHealth() {
      try {
        const data = await fetchHealth();
        workbench.isConnected = true;
        workbench.mcpConnected = !!data.mcp_connected;
        workbench.provider = (data.provider as 'anthropic' | 'openai' | 'claude_cli' | 'anthropic_api') || 'unknown';
        workbench.providerModel = data.model_routing?.optimize || '';
        workbench.githubOAuthEnabled = !!data.github_oauth_enabled;
      } catch {
        workbench.isConnected = false;
        workbench.mcpConnected = false;
      }
    }

    pollHealth();
    healthTimer = setInterval(pollHealth, 15_000);

    // GitHub hydration moved to $effect below — runs reactively when auth.isAuthenticated becomes true

    // Ensure at least one tab is open
    editor.ensureWelcomeTab();

    // Register command palette commands
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
      id: 'forge.retry',
      label: 'Retry Last Optimization',
      description: 'Re-run with same settings',
      group: 'Forge',
      action: () => {
        if (forge.optimizationId) forge.retryForge(forge.optimizationId);
      }
    });
    commandPalette.registerCommand({
      id: 'history.open',
      label: 'Open History',
      group: 'History',
      action: () => {
        workbench.setActivity('history');
      }
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
    commandPalette.registerCommand({
      id: 'nav.settings',
      label: 'Open Settings',
      group: 'Navigation',
      action: () => {
        workbench.setActivity('settings');
      }
    });
    commandPalette.registerCommand({
      id: 'nav.toggle-navigator',
      label: 'Toggle Navigator',
      shortcut: 'Ctrl+B',
      group: 'Navigation',
      action: () => {
        workbench.toggleNavigator();
      }
    });
    commandPalette.registerCommand({
      id: 'nav.toggle-inspector',
      label: 'Toggle Inspector',
      shortcut: 'Ctrl+.',
      group: 'Navigation',
      action: () => {
        workbench.toggleInspector();
      }
    });

    return () => {
      clearInterval(healthTimer);
      window.removeEventListener('resize', handleResize);
      document.removeEventListener('keydown', handleF6);
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
    <div style="grid-row: 2; grid-column: 1 / -1;">
      <StatusBar />
    </div>
  </div>

  <!-- Global overlays -->
  <CommandPalette />
  <ToastContainer />
  {#if showOnboarding}
    <OnboardingModal onComplete={() => { showOnboarding = false; }} />
  {/if}
{/if}
