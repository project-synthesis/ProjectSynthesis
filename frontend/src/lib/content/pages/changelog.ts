/**
 * Dynamic changelog — parses docs/CHANGELOG.md at build time.
 *
 * Vite's `?raw` import gives us the markdown string.  The parser converts
 * Keep a Changelog format into the TimelineSection structure the existing
 * Timeline.svelte component expects.  No manual updates needed — push a
 * new CHANGELOG.md entry and it appears automatically.
 */
import type { ContentPage, TimelineSection } from '../types';
import changelogRaw from '../../../../../docs/CHANGELOG.md?raw';

// ---------------------------------------------------------------------------
// Category color map (matches brand guidelines)
// ---------------------------------------------------------------------------

const CATEGORY_COLORS: Record<string, string> = {
  ADDED: 'var(--color-neon-green)',
  CHANGED: 'var(--color-neon-yellow)',
  FIXED: 'var(--color-neon-cyan)',
  REMOVED: 'var(--color-neon-red)',
};

type CategoryLabel = 'ADDED' | 'CHANGED' | 'FIXED' | 'REMOVED';

// ---------------------------------------------------------------------------
// Markdown parser — Keep a Changelog → TimelineSection['versions']
// ---------------------------------------------------------------------------

function parseChangelog(raw: string): TimelineSection['versions'] {
  const versions: TimelineSection['versions'] = [];
  let currentVersion: TimelineSection['versions'][number] | null = null;
  let currentCategory: { label: CategoryLabel; color: string; items: string[] } | null = null;

  for (const line of raw.split('\n')) {
    // ## Version header: "## v0.3.19-dev — 2026-04-09" or "## Unreleased"
    const versionMatch = line.match(/^## (.+)/);
    if (versionMatch) {
      // Skip the file title "# Changelog"
      if (line.startsWith('# ')) continue;

      // Flush previous version
      if (currentCategory && currentVersion) {
        currentVersion.categories.push(currentCategory);
        currentCategory = null;
      }
      if (currentVersion && currentVersion.categories.length > 0) {
        versions.push(currentVersion);
      }

      const header = versionMatch[1].trim();
      if (header.toLowerCase() === 'unreleased') {
        currentVersion = { version: 'Unreleased', date: '', categories: [] };
      } else {
        // "v0.3.19-dev — 2026-04-09"
        const parts = header.split(/\s+[—–-]\s+/);
        currentVersion = {
          version: parts[0]?.trim() || header,
          date: parts[1]?.trim() || '',
          categories: [],
        };
      }
      continue;
    }

    // ### Category header: "### Added", "### Changed", "### Fixed", "### Removed"
    const catMatch = line.match(/^### (\w+)/);
    if (catMatch && currentVersion) {
      if (currentCategory) {
        currentVersion.categories.push(currentCategory);
      }
      const label = catMatch[1].toUpperCase() as CategoryLabel;
      currentCategory = {
        label,
        color: CATEGORY_COLORS[label] || 'var(--color-text-secondary)',
        items: [],
      };
      continue;
    }

    // - Item line: "- **Bold title** — description" → strip to plain text
    const itemMatch = line.match(/^- (.+)/);
    if (itemMatch && currentCategory) {
      // Strip markdown bold markers for clean display
      const text = itemMatch[1].replace(/\*\*/g, '').trim();
      if (text) currentCategory.items.push(text);
    }
  }

  // Flush final version
  if (currentCategory && currentVersion) {
    currentVersion.categories.push(currentCategory);
  }
  if (currentVersion && currentVersion.categories.length > 0) {
    versions.push(currentVersion);
  }

  // Filter out empty "Unreleased" if it has no items
  return versions.filter((v) => v.categories.length > 0);
}

// ---------------------------------------------------------------------------
// Export — ContentPage consumed by the [slug] route
// ---------------------------------------------------------------------------

export const changelog: ContentPage = {
  slug: 'changelog',
  title: 'What Changed and When.',
  description:
    'Release history for Project Synthesis. All notable changes, additions, and fixes.',
  sections: [
    {
      type: 'hero',
      heading: 'WHAT CHANGED AND WHEN.',
      subheading:
        'All notable changes to Project Synthesis, in reverse chronological order.',
    },
    {
      type: 'timeline',
      versions: parseChangelog(changelogRaw),
    },
  ],
};
