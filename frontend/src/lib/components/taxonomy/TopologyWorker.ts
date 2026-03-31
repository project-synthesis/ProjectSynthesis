/**
 * Semantic gravity n-body simulation for taxonomy topology layout.
 *
 * Five forces create a galaxy-like distribution where related nodes
 * naturally cluster while maintaining overall spatial clarity:
 *
 * 1. UMAP anchor spring — preserves semantic positioning from UMAP
 * 2. Parent-child spring — domain hubs attract their children (solar systems)
 * 3. Same-domain affinity — clusters in the same domain attract gently
 * 4. Universal repulsion — inverse-square push creates spacing
 * 5. Collision resolution — prevents node overlap
 *
 * The result: domain groups form visible neighborhoods (galaxies),
 * with related cross-domain nodes bridging between groups.
 *
 * Budget: <100ms for 200 nodes at 60 iterations.
 */

export interface WorkerInput {
  positions: Float32Array;      // [x0,y0,z0, x1,y1,z1, ...]
  restPositions: Float32Array;  // UMAP positions — semantic anchor points
  sizes: Float32Array;          // [s0, s1, ...]
  parentIndices: Int32Array;    // parent index per node (-1 = root/no parent)
  domainGroups: Int32Array;     // domain group ID (same domain = same int)
  iterations: number;
}

export interface WorkerOutput {
  positions: Float32Array;
  elapsed: number;
}

// --- Force constants ---
// Tuned for 20-200 nodes in UMAP-scaled space (~[-10, 10] per axis).

/** Pull toward UMAP rest position. Preserves semantic meaning. */
const ANCHOR = 0.007;
/** Parent-child spring strength. Creates solar-system groupings. */
const PARENT_SPRING = 0.04;
/** Desired parent-child distance. Children orbit at this radius. */
const PARENT_REST_LEN = 9.0;
/** Same-domain mutual attraction. Gentle — groups neighborhoods, not blobs. */
const DOMAIN_ATTRACT = 0.006;
/** Max distance for domain attraction. */
const DOMAIN_RANGE = 25;
/** Inverse-square repulsion. Dominant force — creates spatial spread. */
const REPULSION = 0.7;
/** Distance beyond which repulsion is negligible. */
const REPULSION_RANGE = 45;
/** Collision repulsion (strong, short range). Prevents overlap. */
const COLLISION_STRENGTH = 1.2;
/** Pull toward centroid. Very gentle — just prevents infinite drift. */
const CENTERING = 0.001;
/** Velocity decay per iteration. */
const DAMPING = 0.88;

/**
 * Run the semantic gravity simulation synchronously.
 */
export function settleForces(input: WorkerInput): WorkerOutput {
  const { positions, restPositions, sizes, parentIndices, domainGroups, iterations } = input;
  const n = sizes.length;
  if (n === 0) return { positions: new Float32Array(0), elapsed: 0 };
  if (positions.length !== n * 3) {
    throw new Error(
      `settleForces: positions.length (${positions.length}) must equal sizes.length * 3 (${n * 3})`
    );
  }
  const start = performance.now();

  const pos = new Float32Array(positions);
  const rest = restPositions;
  const vel = new Float32Array(n * 3);
  const force = new Float32Array(n * 3);

  for (let iter = 0; iter < iterations; iter++) {
    force.fill(0);

    // --- 1. Compute centroid ---
    let cx = 0, cy = 0, cz = 0;
    for (let i = 0; i < n; i++) {
      cx += pos[i * 3];
      cy += pos[i * 3 + 1];
      cz += pos[i * 3 + 2];
    }
    cx /= n; cy /= n; cz /= n;

    // --- 2. Pairwise forces: repulsion + domain attraction ---
    for (let i = 0; i < n; i++) {
      const ix = i * 3, iy = ix + 1, iz = ix + 2;
      const di = domainGroups[i];
      for (let j = i + 1; j < n; j++) {
        const jx = j * 3, jy = jx + 1, jz = jx + 2;

        const dx = pos[ix] - pos[jx];
        const dy = pos[iy] - pos[jy];
        const dz = pos[iz] - pos[jz];
        const distSq = dx * dx + dy * dy + dz * dz;
        const dist = Math.sqrt(distSq) || 0.001;

        if (dist > REPULSION_RANGE) continue;

        // Universal inverse-square repulsion
        const repF = REPULSION / (distSq + 0.5);
        const repNorm = repF / dist;
        force[ix] += dx * repNorm;
        force[iy] += dy * repNorm;
        force[iz] += dz * repNorm;
        force[jx] -= dx * repNorm;
        force[jy] -= dy * repNorm;
        force[jz] -= dz * repNorm;

        // Same-domain attraction — pull related nodes together
        if (di === domainGroups[j] && dist < DOMAIN_RANGE && dist > 1.0) {
          const attrF = DOMAIN_ATTRACT / dist; // linear falloff
          force[ix] -= dx * attrF;
          force[iy] -= dy * attrF;
          force[iz] -= dz * attrF;
          force[jx] += dx * attrF;
          force[jy] += dy * attrF;
          force[jz] += dz * attrF;
        }

        // Collision resolution (strong, short range)
        const minDist = (sizes[i] + sizes[j]) * 0.6;
        if (dist < minDist) {
          const collF = COLLISION_STRENGTH * (minDist - dist) / dist;
          force[ix] += dx * collF;
          force[iy] += dy * collF;
          force[iz] += dz * collF;
          force[jx] -= dx * collF;
          force[jy] -= dy * collF;
          force[jz] -= dz * collF;
        }
      }
    }

    // --- 3. Per-node forces ---
    for (let i = 0; i < n; i++) {
      const ix = i * 3, iy = ix + 1, iz = ix + 2;

      // UMAP anchor spring — gentle pull toward semantic rest position
      force[ix] -= (pos[ix] - rest[ix]) * ANCHOR;
      force[iy] -= (pos[iy] - rest[iy]) * ANCHOR;
      force[iz] -= (pos[iz] - rest[iz]) * ANCHOR;

      // Centering — very gentle drift prevention
      force[ix] -= (pos[ix] - cx) * CENTERING;
      force[iy] -= (pos[iy] - cy) * CENTERING;
      force[iz] -= (pos[iz] - cz) * CENTERING;

      // Parent-child spring — attract toward parent at rest length
      const pi = parentIndices[i];
      if (pi >= 0 && pi < n) {
        const px = pi * 3, py = px + 1, pz = px + 2;
        const dx = pos[px] - pos[ix];
        const dy = pos[py] - pos[iy];
        const dz = pos[pz] - pos[iz];
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.001;
        // Spring: pulls when far, pushes when too close
        const springF = PARENT_SPRING * (dist - PARENT_REST_LEN) / dist;
        force[ix] += dx * springF;
        force[iy] += dy * springF;
        force[iz] += dz * springF;
        // Newton's 3rd law: parent feels the pull too (lighter)
        force[px] -= dx * springF * 0.3;
        force[py] -= dy * springF * 0.3;
        force[pz] -= dz * springF * 0.3;
      }
    }

    // --- 4. Integrate with velocity damping ---
    for (let i = 0; i < n; i++) {
      const ix = i * 3, iy = ix + 1, iz = ix + 2;
      vel[ix] = (vel[ix] + force[ix]) * DAMPING;
      vel[iy] = (vel[iy] + force[iy]) * DAMPING;
      vel[iz] = (vel[iz] + force[iz]) * DAMPING;
      pos[ix] += vel[ix];
      pos[iy] += vel[iy];
      pos[iz] += vel[iz];
    }
  }

  return { positions: pos, elapsed: performance.now() - start };
}

// Worker message handler (only active when loaded as Web Worker)
if (typeof self !== 'undefined' && typeof (self as any).importScripts === 'function') {
  self.onmessage = (event: MessageEvent<WorkerInput>) => {
    const result = settleForces(event.data);
    (self as any).postMessage(result, [result.positions.buffer]);
  };
}
