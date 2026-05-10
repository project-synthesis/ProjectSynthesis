# 3D Visualization Reference

The Pattern Graph / Taxonomy 3D scene is the only WebGL surface in Project Synthesis. This reference codifies the medium-specific rules. The 5 brand axioms (Signal Over Noise, Neon Tube Model, Darkness as Active Design, Chromatic Encoding, Mechanical Responsiveness) apply verbatim — these rules are how those axioms translate into Three.js / WebGL.

## Table of Contents
- [Material Recipes](#material-recipes)
- [Lighting Setups](#lighting-setups)
- [Post-Processing Decision Tree](#post-processing-decision-tree)
- [Spring Physics Constants](#spring-physics-constants)
- [Edge Geometry Recipes](#edge-geometry-recipes)
- [Per-Frame Allocation Budget](#per-frame-allocation-budget)
- [Disposal Contract](#disposal-contract)
- [Shader Uniform Patterns](#shader-uniform-patterns)

---

## Material Recipes

The canonical 3D emission technique. Replaces every "glow" instinct.

| Mesh role | Material | `color` driver | `emissive` driver | `emissiveIntensity` driver |
|-----------|----------|----------------|-------------------|----------------------------|
| Cluster sphere | `MeshStandardMaterial` | Domain hex from chromatic palette | Same domain hex | Member-count signal (0.6 base → 1.4 hero, clamped) |
| Cluster sphere (hovered) | same | unchanged | unchanged | `1.0 + 0.4 * hoverIntensity` |
| Edge line | `LineBasicMaterial` | Source-cluster domain hex blended toward target | n/a | Use `transparent: true` + `opacity` for relationship strength |
| Selection ring | `MeshStandardMaterial` | `--color-neon-cyan` | same | `1.0` constant |
| Domain anchor | `MeshStandardMaterial` | Domain hex | same | `0.4` (recessed — domain is context, clusters are foreground) |

**Rules:**
- `MeshStandardMaterial` is the default — it accepts emission, responds to lighting, supports `roughness` for surface character. Cluster meshes use `roughness: 0.6` (matte — no specular halos), `metalness: 0.0`.
- `emissiveIntensity` is **always** driven by data, never a constant aesthetic value. The intensity *is* the chromatic encoding signal.
- For LineBasicMaterial edges, `linewidth` is ignored on most platforms — use `Line2` from `three/examples/jsm/lines/Line2.js` if visible thickness > 1px is required (rare; default to 1px).
- Never use `MeshBasicMaterial` for clusters — it bypasses lighting and produces flat un-emissive surfaces that contradict the Neon Tube Model's "lit emission" axiom.

---

## Lighting Setups

Real 3D lighting is permitted (and required for `emissive` to read correctly). Effects-as-lighting is banned.

| Light type | Permitted? | Defaults | Use case |
|------------|------------|----------|----------|
| `AmbientLight` | Yes | intensity `0.3`, color `#ffffff` | Baseline scene fill so non-emissive surfaces remain visible |
| `DirectionalLight` | Yes | intensity `0.7`, position `(5, 10, 5)`, color `#ffffff`, `castShadow: true` | Primary key light. ShadowMap enabled for depth cueing |
| `HemisphereLight` | Yes (optional) | intensity `0.2`, sky `#1a1a2e`, ground `#06060c` | Top-down organic feel matching the dark void background |
| `PointLight` | **Avoid** | n/a | Halo-mimicry anti-pattern. Only permitted as a hero focal light on the user-selected cluster, never as ambient atmosphere |
| `SpotLight` | **No** | n/a | Cone-shaped halos contradict the zero-effects directive |

**ShadowMap defaults** (when `castShadow: true`):
- `renderer.shadowMap.enabled = true`
- `renderer.shadowMap.type = THREE.PCFShadowMap` (PCFSoftShadowMap was deprecated in three@0.170+; PCFShadowMap is the canonical replacement)
- Light `shadow.mapSize.set(1024, 1024)` (2048 for hero scenes if perf budget allows)
- Mesh `castShadow = true` for clusters, `receiveShadow = true` for the floor plane (if any)

ShadowMaps encode 3D depth, not 2D fake-shadow effects — they're allowed.

---

## Post-Processing Decision Tree

The simple rule: **don't**. Three exceptions.

| Pass | Permitted? | Reason |
|------|------------|--------|
| `UnrealBloomPass` / `BloomPass` | **NO** | 2D-glow rendered onto a 3D scene. Same anti-pattern as `text-shadow: 0 0 8px`, different layer |
| `FilmPass` / `GrainPass` | **NO** | Pure noise contradicts Signal Over Noise (axiom 1) |
| `GodRaysPass` | **NO** | Volumetric halo emission |
| `GlitchPass` / `DotScreenPass` / etc. | **NO** | Decorative, no data signal |
| `RGBShiftPass` | **NO** | Aesthetic, not encoding data |
| `SMAAPass` / `FXAAPass` | **Yes** | Anti-aliasing — improves edge sharpness, supports the Neon Tube Model's "uniform 1px width" rule on rendered edges |
| `ToneMappingPass` | **Yes (rare)** | Only when doing PBR with HDR colors. Default `renderer.toneMapping = THREE.NoToneMapping` is fine |

If post-processing is added, every pass must be disposed in cleanup (see [Disposal Contract](#disposal-contract)).

---

## Spring Physics Constants

Continuous motion is the medium's native idiom. The project's existing constants are canonical.

| Constant | Value | Source | Use |
|----------|-------|--------|-----|
| `k` (spring stiffness) | `120.0` | `frontend/src/lib/components/taxonomy/ClusterPhysics.ts` | Cluster scale spring, hover lift |
| `d` (damping coefficient) | `12.0` | same | Velocity decay per frame |
| `dt` clamp | `0.1` (s) | same | Maximum per-frame timestep — prevents catastrophic overshoot on tab-switch resume |
| `velocityFloor` | `1e-4` | derived | Below this magnitude, snap to target + zero velocity to avoid infinite micro-oscillation |
| Catenary sag coefficient | `0.15 * distance` | `PlasmaBeam.ts` | Edge droop magnitude as a fraction of cluster-to-cluster distance |

**Integration pattern** (always semi-implicit Euler — no Runge-Kutta complexity):
```ts
// Per-frame
const dt = Math.min((now - lastTime) / 1000, MAX_DT);
const force = k * (target - current) - d * velocity;
velocity += force * dt;
current += velocity * dt;
if (Math.abs(velocity) < velocityFloor && Math.abs(target - current) < velocityFloor) {
  current = target;
  velocity = 0;
}
```

---

## Edge Geometry Recipes

| Edge style | Geometry | Segment count | LOD policy |
|------------|----------|---------------|------------|
| Cluster-to-cluster relationship | `BufferGeometry` with sampled catenary curve | 16 default | 8 at distance > 50 units, 32 at hero (selected pair) |
| Selection highlight | `EdgesGeometry` from cluster mesh | n/a | Always shown when cluster is selected |
| Domain boundary indicator | `Line` between domain anchor + cluster | 1 segment (straight) | Always full resolution |

**Catenary droop pattern:**
```ts
const sag = distance * 0.15;
control.copy(midpoint);
control.y -= sag;
const curve = new THREE.QuadraticBezierCurve3(start, control, end);
const points = curve.getPoints(SEGMENT_COUNT);
geometry.setFromPoints(points);
```

Reuse a single `_scratchControl: THREE.Vector3` module-level — never allocate inside the per-frame loop (see [Per-Frame Allocation Budget](#per-frame-allocation-budget)).

---

## Per-Frame Allocation Budget

**Zero allocations in the render loop.** This is a hard invariant.

Every `new THREE.Vector3()` / `new THREE.Color()` / `new THREE.Quaternion()` / `new THREE.Matrix4()` inside an `onBeforeRender` / animation callback / forEach over clusters is a GC pressure leak. With ~50 clusters at 60fps, allocating one Vector3 per cluster per frame produces ~3000 allocs/sec — visible jank on integrated GPUs and contradicts the "precision instrument" brand promise.

**Canonical scratch table** (declare at module top, reuse forever):
```ts
const _scratchVec3a = new THREE.Vector3();
const _scratchVec3b = new THREE.Vector3();
const _scratchVec3c = new THREE.Vector3();
const _scratchQuat = new THREE.Quaternion();
const _scratchColor = new THREE.Color();
const _scratchMat4 = new THREE.Matrix4();
```

**Anti-pattern → replacement:**
| Anti-pattern | Replacement |
|--------------|-------------|
| `mesh.scale.lerp(new THREE.Vector3(s, s, s), 0.1)` | `mesh.scale.lerp(_scratchVec3a.set(s, s, s), 0.1)` |
| `obj.rotateOnAxis(new THREE.Vector3(0, 0, 1), 0.02)` | Module-level `const Z_AXIS = new THREE.Vector3(0, 0, 1).normalize();` then `obj.rotateOnAxis(Z_AXIS, 0.02)` |
| `target = a.clone().add(b)` | `target.copy(a).add(b)` |
| `new THREE.Color().setHSL(...)` per frame | `_scratchColor.setHSL(...)` |

If the loop body genuinely needs more scratch instances than the canonical 6, declare them at module top and document why.

---

## Disposal Contract

Every created GPU resource must reach a `dispose()` call from cleanup. GPU memory leaks contradict "precision instrument."

**Mandatory disposal targets:**
| Resource | Disposal |
|----------|----------|
| `BufferGeometry` | `geometry.dispose()` |
| `Material` | `material.dispose()` (or each in `material[]` if array) |
| `Texture` (incl. `CanvasTexture`, `DataTexture`) | `texture.dispose()` |
| `WebGLRenderTarget` | `renderTarget.dispose()` |
| `EffectComposer` | `composer.dispose()` + dispose every pass on `composer.passes[]` |
| `WebGLRenderer` | `renderer.dispose()` + `renderer.forceContextLoss()` (releases the GL context) |
| Animation callbacks (`requestAnimationFrame`-based) | Call the cancel function returned at creation site |

**Canonical pattern — disposables list:**
```ts
const disposables: Array<() => void> = [];

// At creation site:
const geom = new THREE.SphereGeometry(...);
disposables.push(() => geom.dispose());

const mat = new THREE.MeshStandardMaterial(...);
disposables.push(() => mat.dispose());

// Cleanup:
for (const dispose of disposables) dispose();
disposables.length = 0;
```

**Checklist for `onMount` / Svelte cleanup:**
- [ ] Every `new THREE.*Geometry(...)` has a matching `dispose()`
- [ ] Every `new THREE.*Material(...)` has a matching `dispose()`
- [ ] Every `new THREE.*Texture(...)` has a matching `dispose()`
- [ ] Every `requestAnimationFrame`-driven animation has its cancel returned + invoked
- [ ] `renderer.dispose()` + `renderer.forceContextLoss()` called
- [ ] If a `composer` exists: `composer.dispose()` + each pass disposed
- [ ] No `globalThis.__*` references remain (use module-level `let` if state must persist across HMR; dispose explicitly)

---

## Shader Uniform Patterns

If custom `ShaderMaterial` is required (rare — prefer `MeshStandardMaterial` + `emissive` for 99% of cases), follow these rules.

**Uniform naming — Canon Terminology applies:**

| Use | Avoid |
|-----|-------|
| `uContourColor` | `uGlowColor` |
| `uEmissiveIntensity` | `uGlowIntensity` |
| `uFlashTrigger` | `uPulseTrigger` |
| `uTintAlpha` | `uHaloAlpha` |
| `uPulseDuration` | `uRadianceDecay` |

**Use uniforms for** (mutable per-frame or per-instance):
- Per-cluster state (`uHoverIntensity`, `uSelected`)
- Time-driven values (`uTime` for synchronized pulses across multiple meshes)
- Opacity / alpha (when not driven by material `transparent` + `opacity`)
- Domain color when driving multi-color blends inside the shader

**Use `#define` for** (compile-time constants):
- Segment counts, max array sizes, feature flags
- Anything that wouldn't change without a recompile

**Use material properties for** (rarely-changing values):
- Base color (`material.color`)
- Emission (`material.emissive`, `material.emissiveIntensity`)
- Roughness, metalness, opacity threshold

**Per-fragment cost discipline:**
- Avoid `sin` / `cos` / `pow` per fragment unless the visual effect demands it (e.g., synchronized pulse). Cost compounds at high fragment counts on integrated GPUs.
- Wrap optional dynamic effects behind a `uPulseEnabled` boolean uniform so they can be globally toggled for perf-constrained sessions.
- `fract(sin(...) * 43758.5453)` style noise is banned per the zero-effects directive — use deterministic procedural functions if texture variation is needed.
