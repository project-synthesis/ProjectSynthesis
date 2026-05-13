/**
 * Brand compliance test — 3D Pattern Graph scope.
 *
 * Spec: `.claude/skills/brand-guidelines/SKILL.md` "3D Visualization Scope"
 * + `references/3d-visualization.md` Canon Vocabulary + Audit Checklist.
 *
 * The 3D Pattern Graph (`frontend/src/lib/components/taxonomy/`) is exempt
 * from the 2D-UI vocabulary ban (per AGENTS.md amendment + brand guidelines
 * "3D Visualization Scope"). Canonical features use the words `glow`,
 * `halo`, `bloom`, `radiance`, `breathing`, `dust`, `pulse`, `flash` —
 * those are the names of the features (canon F1–F19). Banning them
 * would break the actual canon.
 *
 * What this gate enforces (only the things that genuinely don't carry
 * data signal):
 *
 *   1. Three.js post-processing imports that are explicitly NOT canon
 *      (GlitchPass, DotScreenPass, RGBShiftPass, GodRaysPass) — these
 *      add aesthetic noise without data signal. UnrealBloomPass and
 *      FilmPass ARE canon (F13) and intentionally permitted.
 *   2. Hidden-instruction patterns the brand never permits (e.g. the
 *      old `text-shadow: 0 0 8px` 2D anti-pattern reaching the 3D code).
 *
 * What this gate explicitly does NOT enforce:
 *
 *   - Banned-word regex against 3D scope. The 2D-UI ban is in
 *     SKILL.md and applies to `frontend/src/lib/components/{layout,editor,
 *     refinement,shared,landing}/`, NOT `taxonomy/`.
 */
import { describe, expect, test } from 'vitest';

const SCOPE_FILES = [
  'BeamPool.ts',
  'BeamShader.ts',
  'ClusterPhysics.ts',
  'DomainEdgeShader.ts',  // T1.3 — domain structural edge shader
  'EdgeShader.ts',
  'EnvelopePool.ts',     // canon F19 — plasma envelopement pool
  'EnvelopeShader.ts',   // canon F19 — envelope shader (BeamShader-adapted)
  'focus-math.ts',
  'PlasmaBeam.ts',
  'SemanticTopology.svelte',
  'TopologyData.ts',
  'TopologyInteraction.ts',
  'TopologyLabels.ts',
  'TopologyRenderer.ts',
  'TopologyWorker.ts',
] as const;

// Post-processing passes that are NOT canon — they're aesthetic-only or
// add noise without data signal. UnrealBloomPass + FilmPass are canon
// (F13) and explicitly NOT in this list.
const NON_CANON_POSTPROCESSING_IMPORTS = [
  'GlitchPass',
  'DotScreenPass',
  'RGBShiftPass',
  'GodRaysPass', // banned per canon F13 — aesthetic-only, no data signal
];

const fileContents = import.meta.glob<string>(
  [
    './BeamPool.ts',
    './BeamShader.ts',
    './ClusterPhysics.ts',
    './DomainEdgeShader.ts',
    './EdgeShader.ts',
    './EnvelopePool.ts',
    './EnvelopeShader.ts',
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
  test('no non-canon post-processing imports in any 3D-scope file', () => {
    const violations: string[] = [];
    for (const file of SCOPE_FILES) {
      const content = readScopeFile(file);
      for (const pattern of NON_CANON_POSTPROCESSING_IMPORTS) {
        // Match `from 'three/addons/postprocessing/<Pattern>` to scope this
        // strictly to the import boundary — not random comments or
        // identifier names that happen to contain these substrings.
        const importRegex = new RegExp(`from\\s+['"]three[^'"]*${pattern}`);
        if (importRegex.test(content)) {
          violations.push(`${file}: imports non-canon ${pattern}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });

  test('canonical post-processing imports remain reachable (F13)', () => {
    // Negative regression: if these get accidentally renamed or removed
    // from TopologyRenderer.ts, the cinematic pipeline silently breaks.
    const renderer = readScopeFile('TopologyRenderer.ts');
    expect(renderer).toMatch(
      /from\s+['"]three\/addons\/postprocessing\/EffectComposer\.js['"]/,
    );
    expect(renderer).toMatch(
      /from\s+['"]three\/addons\/postprocessing\/RenderPass\.js['"]/,
    );
    expect(renderer).toMatch(
      /from\s+['"]three\/addons\/postprocessing\/UnrealBloomPass\.js['"]/,
    );
    expect(renderer).toMatch(
      /from\s+['"]three\/addons\/postprocessing\/FilmPass\.js['"]/,
    );
    expect(renderer).toMatch(
      /from\s+['"]three\/addons\/postprocessing\/SMAAPass\.js['"]/,
    );
  });
});
