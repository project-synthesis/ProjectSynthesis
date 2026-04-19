/**
 * Centralized tooltip definitions for UI controls (buttons, toggles, actions).
 *
 * For metric/score tooltips → see metric-tooltips.ts
 * For MCP force-toggle tooltips → see mcp-tooltips.ts
 *
 * Plain language — no jargon. Import from here, never inline in components.
 */

// ---------------------------------------------------------------------------
// Navigator: Routing controls
// ---------------------------------------------------------------------------

export const ROUTING_TOOLTIPS = {
  force_sampling_label:
    "Use IDE's LLM for the 3-phase pipeline via MCP sampling",
  force_passthrough_label:
    'Bypass all pipelines — returns assembled template for manual processing',
};

// ---------------------------------------------------------------------------
// Navigator: Scoring mode
// ---------------------------------------------------------------------------

export const SCORING_TOOLTIPS = {
  heuristic: 'Heuristic-only scoring (no LLM scorer in passthrough mode)',
  hybrid: 'LLM + heuristic blended scores',
};

// ---------------------------------------------------------------------------
// Navigator: Strategy & History
// ---------------------------------------------------------------------------

export const STRATEGY_TOOLTIPS = {
  edit_template: 'Edit template',
  feedback_positive: 'Positive',
  feedback_negative: 'Negative',
};

// ---------------------------------------------------------------------------
// Inspector: Cluster management
// ---------------------------------------------------------------------------

export const INSPECTOR_TOOLTIPS = {
  save: 'Save',
  cancel: 'Cancel',
  rename: 'Click to rename',
  close_detail: 'Close family detail',
  promote: 'Promote this cluster to a proven template',
  promote_blocked: 'Requires avg score \u2265 6.0 and 3+ members or 1+ pattern usage',
  demote: 'Demote this template back to active state',
  unarchive: 'Restore this cluster to active state',
  score_toggle_avg: 'Show average',
  score_toggle_dim: 'Show per-dimension',
};

// ---------------------------------------------------------------------------
// Cluster navigator
// ---------------------------------------------------------------------------

export const CLUSTER_NAV_TOOLTIPS = {
  total_clusters: 'Total clusters',
  open_mindmap: 'Open pattern mindmap',
  members_badge: 'Members',
  use_template: 'Use this template',
  highlight_graph: 'Click to highlight in graph',
  usage_count: 'Pattern usage count',
  avg_score: 'Average score',
  similarity_score: 'Centroid cosine similarity to search text',
};

// ---------------------------------------------------------------------------
// Forge artifact (editor actions)
// ---------------------------------------------------------------------------

export const ARTIFACT_TOOLTIPS = {
  show_original: 'Show original',
  show_optimized: 'Show optimized',
  show_raw: 'Show raw text',
  render_markdown: 'Render markdown',
  view_diff: 'View diff',
  good_result: 'Good result',
  poor_result: 'Poor result',
  copy: 'Copy to clipboard',
};

// ---------------------------------------------------------------------------
// Topology controls
// ---------------------------------------------------------------------------

export const TOPOLOGY_TOOLTIPS = {
  toggle_similarity: 'Toggle similarity edges',
  toggle_injection: 'Toggle injection provenance edges',
  recluster: 'Trigger taxonomy recluster (cold path)',
};

// ---------------------------------------------------------------------------
// Editor groups
// ---------------------------------------------------------------------------

export const EDITOR_TOOLTIPS = {
  new_prompt: 'New prompt — reset and start fresh',
};

// ---------------------------------------------------------------------------
// Activity bar
// ---------------------------------------------------------------------------

export const ACTIVITY_TOOLTIPS = {
  brand: 'Project Synthesis',
};

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

export const STATUS_TOOLTIPS = {
  mcp_disconnected: 'MCP client disconnected',
};

// ---------------------------------------------------------------------------
// Shared components
// ---------------------------------------------------------------------------

export const TIER_TOOLTIPS = {
  degraded: 'Requested tier unavailable',
};

// ---------------------------------------------------------------------------
// GitHub: Index & file count
// ---------------------------------------------------------------------------

export const GITHUB_TOOLTIPS = {
  indexed_file_count:
    'INDEXED SOURCE FILES\n' +
    'Files embedded for semantic retrieval.\n' +
    '\n' +
    'EXCLUDED FROM INDEX\n' +
    ' \u2022 Test dirs: tests/ test/ __tests__/ spec/ specs/\n' +
    '   cypress/ playwright/ e2e/ fixtures/ testdata/\n' +
    '   __mocks__/ __snapshots__/\n' +
    ' \u2022 Test files: *.test.* *.spec.* *.stories.*\n' +
    '   test_* tests_* *_test.* *_spec.* *_bench.*\n' +
    ' \u2022 Test infra: conftest.py jest.config.* vitest.config.*\n' +
    '   playwright.config.* cypress.config.* pytest.ini\n' +
    '   tox.ini noxfile.py .coveragerc\n' +
    ' \u2022 CI/editor: .github/workflows/ .github/ISSUE_TEMPLATE/\n' +
    '   .vscode/ .idea/\n' +
    ' \u2022 Lock files: package-lock.json pnpm-lock.yaml\n' +
    '   npm-shrinkwrap.json composer.lock\n' +
    ' \u2022 Large files: > 100 KB\n' +
    ' \u2022 Non-source: binaries, images, unlisted extensions\n' +
    '\n' +
    'INDEXED EXTENSIONS\n' +
    ' .py .js .ts .jsx .tsx .svelte .vue .go .rs\n' +
    ' .java .rb .c .cpp .h .hpp .cs .swift .kt .scala\n' +
    ' .md .txt .yaml .yml .json .toml .cfg .ini\n' +
    ' .html .css .scss .sh .bash .zsh .fish .sql .graphql',
};

export const PASSTHROUGH_TOOLTIPS = {
  guide_btn: 'How passthrough works',
};
