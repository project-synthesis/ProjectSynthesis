/**
 * Brand compliance test — Pattern Graph 3D scope.
 *
 * Spec: docs/superpowers/specs/2026-05-09-pattern-graph-depth-design.md § 4.4
 * Brand: .claude/skills/brand-guidelines/SKILL.md "3D Visualization Scope" + Canon Terminology
 *        .claude/skills/brand-guidelines/references/3d-visualization.md
 *
 * Two assertions:
 *   1. No banned post-processing imports in 3D taxonomy code.
 *   2. No banned-word matches (glow|radiance|bloom|halo) in 3D taxonomy code,
 *      with the exception of test files mentioning banned terms inside string/regex
 *      literals asserting their absence.
 *
 * Word-boundary regex (`\b...\b`) avoids false-positive matches on substrings
 * like `globalThis`, `globe`, `bloomberg`, etc. Verified: `globalThis` is NOT
 * flagged by `\bglow\b`.
 *
 * Uses Vite's `import.meta.glob` with `?raw` query to load file contents at
 * test-run time without relying on node:fs (avoids @types/node dependency).
 */
import { describe, expect, test } from 'vitest';

// 3D-scope files (frontend/src/lib/components/taxonomy/). 2D UI files
// (DomainReadiness*, DomainStabilityMeter, RebuildSubDomainsModal,
// SubDomainEmergenceList) are out of scope per spec § non-goal #6.
const SCOPE_FILES = [
  'BeamPool.ts',
  'BeamShader.ts',
  'ClusterPhysics.ts',
  'EdgeShader.ts',
  'focus-math.ts',
  'PlasmaBeam.ts',
  'SemanticTopology.svelte',
  'TopologyData.ts',
  'TopologyInteraction.ts',
  'TopologyLabels.ts',
  'TopologyRenderer.ts',
  'TopologyWorker.ts',
] as const;

const BANNED_IMPORT_PATTERNS = [
  'UnrealBloomPass',
  'BloomPass',
  'FilmPass',
  'GodRaysPass',
  'GlitchPass',
  'DotScreenPass',
  'RGBShiftPass',
];

// Word-boundary regex per Canon Terminology. Case-insensitive.
const BANNED_WORD_REGEX = /\b(glow|radiance|bloom|halo)\b/gi;

// Identifier-level denylist per spec § 4.4 ("Identifier-level usage
// (variable names, function names, property accesses) MUST be renamed
// alongside source"). Catches camelCase and PascalCase variants the
// word-boundary regex misses (e.g., `_ensureHaloPool`, `Halo`, `_haloById`).
// Each entry is a substring match — case-insensitive.
const BANNED_IDENTIFIER_SUBSTRINGS = [
  'halo',
  'glowing',  // includes Glow* / GlowingX / etc.
  'bloomPass',
];

// Vite resolves `import.meta.glob` at build/test time. The `?raw` query loads
// each file as a string. Eager mode flattens the lazy module map into direct
// string values keyed by relative path.
const fileContents = import.meta.glob<string>(
  [
    './BeamPool.ts',
    './BeamShader.ts',
    './ClusterPhysics.ts',
    './EdgeShader.ts',
    './focus-math.ts',
    './PlasmaBeam.ts',
    './SemanticTopology.svelte',
    './TopologyData.ts',
    './TopologyInteraction.ts',
    './TopologyLabels.ts',
    './TopologyRenderer.ts',
    './TopologyWorker.ts',
  ],
  { query: '?raw', import: 'default', eager: true },
);

function readScopeFile(name: string): string {
  const key = `./${name}`;
  const content = fileContents[key];
  if (typeof content !== 'string') {
    throw new Error(`brand-compliance: file ${name} not found in glob map`);
  }
  return content;
}

describe('Brand compliance — 3D Pattern Graph scope', () => {
  test('no banned post-processing imports in any 3D-scope file', () => {
    const violations: string[] = [];
    for (const file of SCOPE_FILES) {
      const content = readScopeFile(file);
      for (const pattern of BANNED_IMPORT_PATTERNS) {
        if (content.includes(pattern)) {
          violations.push(`${file}: imports/references "${pattern}"`);
        }
      }
    }
    expect(violations).toEqual([]);
  });

  test('no banned-word identifiers (glow|radiance|bloom|halo) in 3D-scope source', () => {
    const violations: Array<{ file: string; line: number; text: string }> = [];
    for (const file of SCOPE_FILES) {
      const content = readScopeFile(file);
      const lines = content.split('\n');
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        BANNED_WORD_REGEX.lastIndex = 0;
        if (BANNED_WORD_REGEX.test(line)) {
          violations.push({ file, line: i + 1, text: line.trim() });
        }
      }
    }

    if (violations.length > 0) {
      const details = violations
        .map((v) => `  ${v.file}:${v.line} → ${v.text}`)
        .join('\n');
      throw new Error(
        `Banned-word matches found (Canon Terminology — use contour/flash/tint/emission):\n${details}`,
      );
    }
    expect(violations).toEqual([]);
  });

  test('no banned identifier substrings (catches camelCase/PascalCase variants)', () => {
    // Spec § 4.4: identifier-level usage MUST be renamed alongside source.
    // The word-boundary regex above misses identifiers where the banned
    // term is glued to other word characters (e.g., `_ensureHaloPool`,
    // `glowingColor`, `BloomPassClone`). This test catches those.
    const violations: Array<{ file: string; line: number; text: string; pattern: string }> = [];
    for (const file of SCOPE_FILES) {
      const content = readScopeFile(file);
      const lower = content.toLowerCase();
      const lines = content.split('\n');
      for (const pattern of BANNED_IDENTIFIER_SUBSTRINGS) {
        const lowerPattern = pattern.toLowerCase();
        if (!lower.includes(lowerPattern)) continue;
        // Find each occurrence. We walk lines for the report context.
        for (let i = 0; i < lines.length; i++) {
          if (lines[i].toLowerCase().includes(lowerPattern)) {
            violations.push({ file, line: i + 1, text: lines[i].trim(), pattern });
          }
        }
      }
    }

    if (violations.length > 0) {
      const details = violations
        .map((v) => `  ${v.file}:${v.line} (matched "${v.pattern}") → ${v.text}`)
        .join('\n');
      throw new Error(
        `Banned identifier substring matches found (rename to template/emission/contour):\n${details}`,
      );
    }
    expect(violations).toEqual([]);
  });
});
