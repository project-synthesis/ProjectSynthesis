// frontend/src/lib/components/taxonomy/_canon-coverage.test.ts
// Sub-project F (Audit-as-Code) meta-test.
//
// Parses `.claude/skills/brand-guidelines/references/3d-visualization.md`
// `## Audit Checklist` section, extracts every `- [ ] **F<N>**: ...` line,
// and verifies each maps to an `it()` title in `audit-canon.test.ts`.
//
// Failure modes:
// - A canon line was added without a corresponding `it()` in audit-canon.test.ts
// - An `it()` was added but its title doesn't match the canon line's first 8 significant words
//
// Spec: docs/superpowers/specs/2026-05-19-audit-as-code-design.md §3.2

import { describe, expect, it } from 'vitest';

const canonSourceMap = import.meta.glob<string>(
  ['../../../../../.claude/skills/brand-guidelines/references/3d-visualization.md'],
  { query: '?raw', import: 'default', eager: true },
);
const testSourceMap = import.meta.glob<string>(
  ['./audit-canon.test.ts'],
  { query: '?raw', import: 'default', eager: true },
);

function readMapped(map: Record<string, string>, key: string): string {
  const content = map[key];
  if (typeof content !== 'string') throw new Error(`_canon-coverage: ${key} not found`);
  return content;
}

function extractCanonTag(line: string): string {
  // Examples of input lines:
  //   `- [ ] **F1**: cluster fill = MeshStandardMaterial with roughness: 0.6, metalness: 0, ...`
  //   `- [ ] All four impact trigger sites (entrance burst, post-growth burst, ...) route through ImpactCoordinator.fire(...) ...`
  //   `- [ ] Module-level scratch table declared (`_scratchVec3a`, `_scratchQuat`, `_scratchColor`, `Z_AXIS`)`
  //
  // Output: stable identifier the meta-test searches for in audit-canon.test.ts:
  //   `F1: cluster fill MeshStandardMaterial with roughness 06 metalness 0`
  //   `PERF: All four impact trigger sites entrance burst post-growth burst optimization`
  //   `PERF: Module-level scratch table declared _scratchVec3a _scratchQuat _scratchColor`
  const m = line.match(/^- \[ \] (?:\*\*(F\d+)\*\*: )?(.+)$/);
  if (!m) throw new Error(`_canon-coverage: could not parse canon line: ${line}`);
  const tag = m[1] ?? 'PERF';
  const body = m[2]
    .replace(/[`*_]/g, '')
    .replace(/[:,.()]/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 8)
    .join(' ');
  return `${tag}: ${body}`;
}

describe('_canon-coverage — every canon checklist line has a test', () => {
  it('every `- [ ] ...` line in 3d-visualization.md `## Audit Checklist` maps to an it() in audit-canon.test.ts', () => {
    const canonSrc = readMapped(
      canonSourceMap,
      '../../../../../.claude/skills/brand-guidelines/references/3d-visualization.md',
    );
    const testSrc = readMapped(testSourceMap, './audit-canon.test.ts');

    const auditSectionStart = canonSrc.indexOf('## Audit Checklist');
    const auditSectionEnd = canonSrc.indexOf('## Acceptance for changes', auditSectionStart);
    expect(auditSectionStart).toBeGreaterThan(-1);
    expect(auditSectionEnd).toBeGreaterThan(auditSectionStart);

    const auditSection = canonSrc.slice(auditSectionStart, auditSectionEnd);
    const checklistLines = auditSection.match(/^- \[ \] .+$/gm) ?? [];
    expect(checklistLines.length).toBeGreaterThanOrEqual(45); // sanity floor

    const missing: string[] = [];
    for (const line of checklistLines) {
      const tag = extractCanonTag(line);
      if (!testSrc.includes(tag)) missing.push(tag);
    }
    expect(missing, `missing canon-tag mappings in audit-canon.test.ts:\n  ${missing.join('\n  ')}`).toEqual([]);
  });
});
