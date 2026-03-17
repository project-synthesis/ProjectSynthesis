# Project Synthesis — Frontend

SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4. Industrial cyberpunk workbench UI.

## Development

```bash
npm install
npm run dev          # http://localhost:5199
npx svelte-check     # type check
npm run build        # production build (adapter-static)
```

Requires the backend running on port 8000 (`./init.sh start` from project root).

## Architecture

VS Code workbench layout: ActivityBar → Navigator → EditorGroups → Inspector → StatusBar.

| Store | Purpose |
|-------|---------|
| `forge.svelte.ts` | Pipeline state, SSE events, session persistence |
| `preferences.svelte.ts` | User preferences (models, pipeline toggles) |
| `toast.svelte.ts` | Toast notification queue |
| `editor.svelte.ts` | Tab management |
| `github.svelte.ts` | GitHub auth + repo state |
| `refinement.svelte.ts` | Refinement sessions |

See `CLAUDE.md` in project root for full component inventory and brand guidelines.
