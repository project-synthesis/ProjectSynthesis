import type { GraphFamily } from '$lib/api/patterns';

export interface Point { x: number; y: number }

export function calculateDomainAngles(families: GraphFamily[]) {
    const domainMap = new Map<string, GraphFamily[]>();
    for (const f of families) {
      const d = f.domain || 'general';
      if (!domainMap.has(d)) domainMap.set(d, []);
      domainMap.get(d)!.push(f);
    }
    const domains = Array.from(domainMap.keys()).sort();

    const result = new Map<string, { start: number; end: number; mid: number; count: number }>();
    let angleOffset = 0;
    const totalFamilies = Math.max(1, families.length);

    for (const domain of domains) {
      const count = domainMap.get(domain)!.length;
      const sweep = (count / totalFamilies) * Math.PI * 2;
      const start = angleOffset;
      const end = angleOffset + sweep;
      result.set(domain, { start, end, mid: (start + end) / 2, count });
      angleOffset = end;
    }
    return { domainMap, domains, domainAngles: result };
}

export function calculateFamilyPositions(
  domains: string[],
  domainMap: Map<string, GraphFamily[]>,
  domainAngles: Map<string, { start: number; end: number; mid: number; count: number }>,
  cx: number, cy: number, radius: number
) {
  const positions = new Map<string, Point>();
  // Absolute mathematical safeguard against inverted coordinate math
  const safeRadius = Math.max(0, radius);

  for (const domain of domains) {
    const fams = domainMap.get(domain);
    const angles = domainAngles.get(domain);
    
    // Graceful continuum if mappings become desynced
    if (!fams || !angles) continue;
    
    // Bug fix: fams.length instead of fams.length > 1 so a single node centers correctly
    const step = fams.length > 0 ? (angles.end - angles.start) / fams.length : 0;
    
    fams.forEach((f, i) => {
      const angle = angles.start + step * (i + 0.5) - Math.PI / 2;
      const x = cx + Math.cos(angle) * safeRadius;
      const y = cy + Math.sin(angle) * safeRadius;
      
      // Protect string mappings
      if (f.id != null) {
        positions.set(f.id, { x, y });
      }
    });
  }
  return positions;
}

export function calculateEdgePathDistortion(from: Point, to: Point, cx: number, cy: number, strength = 0.3) {
  // Prevent D3 silent SVG crashes on totally null datasets mapping edges before vectors are laid out
  if (!from || !to || typeof from.x !== 'number' || typeof to.x !== 'number') {
    return '';
  }
  
  const midX = (from.x + to.x) / 2 + (cx - (from.x + to.x) / 2) * strength;
  const midY = (from.y + to.y) / 2 + (cy - (from.y + to.y) / 2) * strength;
  
  // Guard against NaN propagating through equations preventing infinite NaN draw loops
  if (Number.isNaN(midX) || Number.isNaN(midY)) return '';

  return `M${from.x},${from.y} Q${midX},${midY} ${to.x},${to.y}`;
}
