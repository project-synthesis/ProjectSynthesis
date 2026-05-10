# 3D Pattern Graph — Brand Reference

The 3D Pattern Graph (taxonomy visualization in `frontend/src/lib/components/taxonomy/`) is the only WebGL surface in Project Synthesis. **It is its own medium with its own grammar.** The 2D-UI rules — "industrial cyberpunk, dark backgrounds, 1px neon contours, no rounded corners, no shadows on UI elements" — apply to the workbench chrome (Navigator, Inspector, ForgeArtifact, ContextPanel, etc.) and **do not apply** to the 3D scene.

This document is the **canon** for the 3D Pattern Graph. An implementation that matches every section here passes the audit. An implementation that diverges either (a) updates this document to match a deliberate redirection, or (b) is wrong.

## Vision

The 3D Pattern Graph is **cinematic, organic, atmospheric, tactile, and high-fidelity**.

| Quality | What it means here |
|---|---|
| **Cinematic** | Post-processing is welcomed where it deepens the data signal. Bloom + film grain + tone-mapping are canonical, not exceptions. |
| **Organic** | Cluster meshes breathe. Spring physics drive scale changes. Edges droop with catenary sag. Hover is not a binary state — it modulates intensity. |
| **Atmospheric** | The 300³ scene void is not empty. Neural Dust (3000 ambient particles) provides depth perception and a galaxy-style backdrop. Domain anchors emit. |
| **Tactile** | Selection fires a plasma beam from the FPS-weapon viewport origin into the target cluster. Beam impacts trigger spring overshoot. Hovers pulse rings. |
| **High-fidelity** | Glow textures via additive blending are correct, not banned. Radial gradients, sprite-mapped point clouds, multi-pass post-processing — all canonical. |

The 3D Pattern Graph is **NOT** sterile, static, flat-shaded, instantaneous, or disconnected. Reactivity is felt, not just computed.

## Canon Vocabulary

The following words refer to **specific data-bearing visual features** described in this document. They are NOT banned — they are the names for the features.

| Word | Canonical referent |
|---|---|
| `glow` | Emission falloff visible at the source — implemented via `MeshStandardMaterial.emissive` + `emissiveIntensity` (cluster fills) AND via additive-blended sprite textures (domain anchor energy cores). |
| `halo` | A banner/template indicator ring drawn around clusters that have been forked into a `PromptTemplate`. Cyan (`0x00e5ff`), `MeshBasicMaterial`, `depthWrite:false`. Implementation pool: `_templateRingPool` (renamed from `_haloPool` for clarity, both names are acceptable in canon vocabulary). |
| `bloom` | The `UnrealBloomPass` post-processing layer that amplifies bright pixels into surrounding ones. Canonical for the 3D Pattern Graph at parameters `(strength: 1.5, radius: 0.4, threshold: 0.85)`. |
| `radiance` | Outgoing light from emissive surfaces post-bloom. The visible signal that conveys "this cluster is alive". |
| `breathing` | Per-frame `±2%` sin-wave scale oscillation on every cluster mesh. Hovered clusters amplify to `±12%`. The data signal: clusters are alive, not freeze-framed. |
| `dust` | The 3000-particle ambient `Points` cloud (`Neural Dust`) that fills the 300³ scene void. Slow X+Y rotation. Provides depth perception. |
| `pulse` | Time-driven shader uniform updates (e.g., `uTime`) that animate edge color/intensity at ~60Hz. Carries data flow signal. |
| `flash` | Brief emission lift on the per-node `MeshStandardMaterial.emissiveIntensity` at beam impact — 80ms cubic ease-out ramp from baseline to 4× baseline (`FLASH_PEAK_MULTIPLIER`), 280ms cubic ease-out decay back. Implemented per F19 (`flashEmissive` + `_tickFlashStates`); fires from the beam's `onImpact` callback so it synchronizes with the plasma envelopement and cluster-physics ripple. |

**Banned terms** are functional, not lexical. The following PASSES are banned because they don't carry data signal — not because their NAMES are bad:

| Banned pass | Reason |
|---|---|
| `GlitchPass` | Decorative noise, no data signal |
| `DotScreenPass` | Decorative pattern overlay |
| `RGBShiftPass` | Aesthetic chromatic aberration, doesn't encode data |

## Canonical Visual Features

Every numbered feature below is **canon**. An audit verifies the implementation matches the parameters listed. Source-of-truth code references in *italics*.

### F1 — Cluster Sphere Fill (the primary data carrier)

- **Geometry:** `IcosahedronGeometry(1, 2)` for clusters; `DodecahedronGeometry(1, 2)` for domain anchors.
- **Material:** `MeshStandardMaterial`
  - `color: new THREE.Color(node.color).multiplyScalar(fillScalar)` where `fillScalar = 0.15` (cluster) / `0.08` (structural)
  - `emissive: new THREE.Color(node.color)` — same domain hex as `color`
  - `emissiveIntensity` — **data-driven**:
    - Cluster: `0.6 + 0.8 * clamp((memberCount - 1) / 49, 0, 1)` → `[0.6, 1.4]`
    - Domain anchor: `0.4` (recessed — context, not foreground)
    - Hovered cluster: multiplied by `1.0 + 0.4 * hoverIntensity` (transient lift)
  - `roughness: 0.6` (matte)
  - `metalness: 0.0`
  - `transparent: true`, `opacity: node.opacity * 0.9`
- **Mesh flags:** `castShadow = true`, `receiveShadow = true` (self-shadowing under directional light)
- **Scale:** initial `mesh.scale.setScalar(node.size)`; per-frame breathing modulates this (see F8)
- *Source: `SemanticTopology.svelte` rebuildScene cluster-fill block*

### F2 — Domain Anchor Vertex Energy Cores

- **Geometry:** `BufferGeometry` from the 20 unique vertices of the base dodecahedron
- **Material:** `PointsMaterial`
  - `color: node.color`
  - `size: 0.35` (deliberately large — accommodates the soft glow falloff)
  - `map: globalThis.__semTopGlowTexture` — a 64×64 `CanvasTexture` built once via `createRadialGradient(32, 32, 0, 32, 32, 32)`:
    - Stop 0: `rgba(255,255,255,1)`
    - Stop 0.3: `rgba(255,255,255,0.8)`
    - Stop 0.6: `rgba(255,255,255,0.2)`
    - Stop 1: `rgba(255,255,255,0)`
  - `transparent: true`, `opacity: node.opacity * 0.95`
  - `blending: THREE.AdditiveBlending` (canonical — produces the "glow" appearance)
  - `depthWrite: false` (so glows don't occlude each other)
  - `sizeAttenuation: true`
- **JSDOM stability:** `if (!ctx) globalThis.__semTopGlowTexture = undefined` — headless tests fall back to no map
- **Disposal:** texture disposed on unmount via `globalThis.__semTopGlowTexture?.dispose()`
- *Source: `SemanticTopology.svelte` domain-anchor branch in cluster-fill block*

### F3 — Template Indicator Ring (`halo`)

- **Geometry:** `RingGeometry(1.25, 1.35, 64)` — 64-segment smooth ring
- **Material:** `MeshBasicMaterial` (banner overlay, not a 3D shaded sphere)
  - `color: 0x00e5ff` (neon cyan)
  - `transparent: true`, `opacity: 0.35` (default)
  - `side: THREE.DoubleSide`
- **Pool:** growable mesh pool — `TEMPLATE_RING_POOL_INITIAL=50` / `GROW_CHUNK=50` / `MAX=500`. High-water mark retained across rebuilds.
- **Hover behavior:** `rotation.z += 0.02` per frame (spin), `material.opacity` oscillates `0.20–0.50` via `0.35 + sin(time * 10) * 0.15`
- **Position sync:** position written every formation-animation frame (avoids the rings-pinned-at-origin bug)
- *Source: `SemanticTopology.svelte` template-ring helpers `_ensureTemplateRingPool` / `_syncTemplateRings`. Renamed in cycle 2 from `_ensureHaloPool` / `_syncHalos`. Both old + new identifier names are acceptable per Canon Vocabulary.*

### F4 — Readiness Ring (per-domain composite tier indicator)

- **Geometry:** `RingGeometry(radius, radius + 0.05, 64)` where `radius = node.size * 1.25`
- **Material:** `MeshBasicMaterial`
  - `color: readinessTierColor(tier)`
  - `transparent: true`, `opacity: node.opacity * READINESS_RING_OPACITY_FACTOR (0.9)`
  - `depthWrite: false` (sits over geometry)
  - `side: THREE.DoubleSide` (visible from below the canopy view)
- **Per-frame billboard:** `_removeReadinessBillboard` callback iterates rings, calls `entry.mesh.lookAt(camera.position)` so the ring stays orthogonal to the view
- **LOD-attenuated opacity:** far `0.4`, mid `0.7`, near `1.0` (multiplied with base)
- *Source: `SemanticTopology.svelte` readiness-ring builder block*

### F5 — Hierarchical Edge (parent → children fan-out)

- **Geometry:** merged `BufferGeometry` of catenary curves between parent and each child
- **Material:** `ShaderMaterial` with `EDGE_DEPTH_VERTEX` / `EDGE_DEPTH_FRAGMENT`
  - Uniforms: `{ uColor, uOpacity, uTime }`
  - `transparent: true`, `depthWrite: false`
- **Per-frame pulse:** `_removeEdgeAnim` callback updates `uTime` value on every registered edge's uniforms — drives shader pulse (data-flow signal)
- **Color:** parent's domain hex (`parentNode.color`)
- **Catenary sag:** `0.15 * distance` (15% of cluster-to-cluster distance)
- *Source: `SemanticTopology.svelte` `buildMergedCurveGeometry` + `EdgeShader.ts`*

### F6 — Similarity / Injection Edge

- **Material:** `LineDashedMaterial` (similarity, dashed) or `LineBasicMaterial` (injection, solid)
- **Color:** `SIMILARITY_EDGE_COLOR` constant or domain hex
- **Toggle-driven visibility** via `clustersStore.showSimilarityEdges` / `showInjectionEdges`
- *Source: `SemanticTopology.svelte` `buildEdgeGroup` shared builder*

### F7 — Plasma Beam Tactile Feedback

- **BeamPool:** 10 pre-allocated `PlasmaBeam` instances (high-water mark, no per-frame allocation)
- **Origin:** NDC `(0, -1, -0.99)` unprojected to world → "FPS weapon viewport" origin (center-bottom of the camera frustum, on the near plane)
- **Trigger:** cluster selection (external selection from sidebar OR `handleNodeClick`)
- **Geometry:** quadratic Bezier curve from origin to target with catenary control point, sampled into a `BufferGeometry` strip
- **Material:** `ShaderMaterial` with custom vertex+fragment from `BeamShader.ts`
- **Lifecycle constants** (`PlasmaBeam.ts`, exported): `FIRING_MS = 700` (perceptible energy transmission — gives the user time to see the catenary head propagate before the cluster reacts; earlier 300ms read as "beam appears, then impact" with no transmission perception), `TERMINATE_MS = 250` (snappier dissipation than the prior 800ms — keeps the click beat tight on rapid re-clicks)
- **Progressive head extension** (`uHead` uniform 0..1): during firing, advances in lockstep with state-time. The fragment shader uses `smoothstep(uHead + 0.04, uHead - 0.02, vUv.x)` to soft-mask alpha past the head — the visible portion of the catenary genuinely extends from origin toward target rather than fading in along its full length. Held at 1.0 throughout sustain + terminate. The cluster sees no light until the head reaches `vUv.x = 1.0`, exactly when `onImpact` fires.
- **Lifetime:** 800ms sustain, then released back to pool
- **Color:** target cluster's `domain hex`
- **Radius:** `Math.max(node.size * 0.04, 0.1)`
- **`onImpact` callback** (canonical anchor point, `BeamConfig.onImpact?: () => void`): fires EXACTLY ONCE at the firing→sustain edge — i.e., when the catenary curve has fully extended and the beam visually impacts the target. All click-impact reactions (cluster physics ripple, plasma envelopement, emissive flash) wire through this callback so they synchronize to actual beam arrival rather than firing synchronously with `acquire()`. **Causal-ordering invariant: every impact reaction MUST fire from `onImpact`, never synchronously with `acquire()`** — otherwise the cluster reacts before the beam arrives.
- *Source: `BeamPool.ts`, `PlasmaBeam.ts`, `BeamShader.ts`*

### F8 — Organic Breathing Oscillation

- **Per-frame callback:** `_breathingAnim`
- **Time accumulator:** `_breathingTime += 0.016` per frame (~60fps)
- **Base:** `scaleBase = sin(_breathingTime * 1.5) * 0.02 + 1.0` → `±2%` oscillation
- **Hover amplification:** `targetScaleMultiplier = isHovered ? scaleBase * 1.1 : scaleBase` → up to `±12%` when hovered
- **Apply to:** every cluster mesh (`mesh.scale.lerp(_scratchVec3a.set(s, s, s), 0.1)`), the parent group's children at the same scale, the readiness ring (`scale.setScalar(targetScaleMultiplier)`), the template ring
- **Allocation budget:** the `_scratchVec3a` borrow in `mesh.scale.lerp(...)` is mandatory — the per-frame `new THREE.Vector3()` form is banned (see "Per-Frame Allocation Budget" below)
- *Source: `SemanticTopology.svelte` `_breathingAnim` callback*

### F9 — Spring Physics on Cluster Scale (Beam Impact Accretion)

- **Module:** `ClusterPhysics.ts`
- **State per cluster:** `{ baseScale, targetScale, scaleVelocity, rippleIntensity }`
- **Constants:** `k = 120.0` (tension), `d = 12.0` (damping), `dt clamp = 0.1s`, `velocityFloor = 1e-4`
- **On beam impact:** `state.targetScale += 0.02` (accretion), `state.rippleIntensity = 1.0`
- **Integration (semi-implicit Euler):**
  ```ts
  const dt = Math.min(delta, 0.1);
  const force = (target - base) * k;
  velocity += force * dt;
  velocity -= velocity * d * dt;
  base += velocity * dt;
  if (|velocity| < floor && |target - base| < floor) { base = target; velocity = 0; }
  ```
- **Snap-to-target floor:** prevents infinite micro-oscillation; makes equality assertions possible (`finalScale === 1.02`).
- **Ripple decay:** `state.rippleIntensity *= 0.92` per frame, zeroed when `< 0.001`
- *Source: `ClusterPhysics.ts`*

### F10 — Neural Dust (Galaxy Backdrop)

- **Geometry:** `BufferGeometry` with 3000-vertex `Float32Array(DUST_COUNT * 3)`
- **Position distribution:** `(Math.random() - 0.5) * 300` per axis — uniform in 300³ cube centered at origin
- **Material:** `PointsMaterial`
  - `color: 0x88ccff` (cool steel-blue)
  - `size: 0.15`
  - `transparent: true`, `opacity: 0.25`
  - `blending: THREE.AdditiveBlending`
  - `depthWrite: false`
- **Per-frame motion:** `_removeDustAnim` callback rotates `_dustPoints.rotation.y += 0.0003`, `rotation.x += 0.0001` — slow drift
- **Userdata tag:** `{ isNeuralDust: true }`
- **Single-instance:** added once per `_dustPoints === null` check; persists across rebuildScene calls
- *Source: `SemanticTopology.svelte` neural-dust block + `_removeDustAnim` callback*

### F11 — Lighting Setup

| Light | Constructor | Position | Notes |
|---|---|---|---|
| `AmbientLight` | `(0xffffff, 0.3)` | n/a | Baseline scene fill |
| `DirectionalLight` | `(0xffffff, 0.7)` | `(5, 10, 5)` | Primary key. `castShadow=true`. `shadow.mapSize.set(1024, 1024)` |
| `HemisphereLight` | `(0x1a1a2e, 0x06060c, 0.2)` | n/a | Sky/ground organic. Sky matches `DEFAULT_BG = 0x06060c` |
| `PointLight` | optional | hero focal | **Permitted** as a focal accent on the user-selected cluster only. Never as ambient atmosphere |
| `SpotLight` | — | — | Banned. Cone falloff contradicts signal-over-noise — it emphasizes empty space rather than a data-bearing surface. (`halo` in this document is reserved for the F3 template indicator ring.) |

### F12 — Shadow Map

- `renderer.shadowMap.enabled = true`
- `renderer.shadowMap.type = THREE.PCFShadowMap` (canonical; `PCFSoftShadowMap` was deprecated in three@0.170+)
- `DirectionalLight.shadow.mapSize.set(1024, 1024)` (2048 for hero scenes if perf budget allows)
- Cluster meshes both `castShadow` AND `receiveShadow` for self-shadowing
- *Source: `TopologyRenderer.ts` constructor + cluster fill block*

### F13 — Post-Processing Pipeline

The 3D Pattern Graph **uses post-processing**. Render via `composer.render()` not `renderer.render()`.

| Pass | Permitted? | Parameters | Reason |
|---|---|---|---|
| `RenderPass` | **Required** | scene + camera | Foundation for the composer chain |
| `UnrealBloomPass` | **Yes** | `(resolution, strength: 1.5, radius: 0.4, threshold: 0.85)` | Amplifies emissive surfaces — the canonical "alive" feeling |
| `FilmPass` | **Yes** | `(intensity: 0.35, grayscale: false)` | Cinematic grain — texture without noise. `intensity ≤ 0.4` keeps it subtle |
| `SMAAPass` / `FXAAPass` | **Yes** | default | Anti-aliasing — supports edge sharpness |
| `ToneMappingPass` | **Yes (rare)** | when doing PBR with HDR colors | Default `THREE.NoToneMapping` is fine for non-HDR |
| `GodRaysPass` / `GlitchPass` / `DotScreenPass` / `RGBShiftPass` | **No** | — | Aesthetic-only or decorative; no data signal. The brand-compliance test bans imports of all four. |

**Composer setup pattern:**
```ts
this.composer = new EffectComposer(this.renderer);
this.composer.addPass(new RenderPass(this.scene, this.camera));
this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(w, h), 1.5, 0.4, 0.85));
this.composer.addPass(new FilmPass(0.35, false));
```

**Disposal:** `composer.dispose()` + each pass disposed; resize via `composer.setSize(w, h)`.

### F14 — Camera Focus (`focusOn`)

- **Adaptive distance default:** when `distance` argument is omitted, retain the user's current zoom: `Math.max(5, Math.min(currentDist, 25))`. Explicit `distance` overrides.
- **Zero-length direction guard:** if `subVectors(startPos, startTarget).lengthSq() < 0.0001`, fall back to `+Z` axis. Prevents NaN cascade through `normalize()` → `multiplyScalar` → `add`.
- **Animation:** 600ms ease-out cubic, `lerpVectors` on both `camera.position` and `controls.target`
- **Cancellation:** in-flight focus animations cancelled on new `focusOn` call (`cancelAnimationFrame(_focusAnimId)`)
- **LOD update:** call `this._checkLod()` per-frame — `OrbitControls.update()` does NOT fire 'change' events when position is mutated programmatically without damping residual; without this call, LOD changes silently lag
- *Source: `TopologyRenderer.ts` `focusOn` + `focus-math.ts` (pure helper for endpoint computation)*

### F15 — Click → Selection Flow

`handleNodeClick(nodeId)` does the **minimum**: `clustersStore.selectCluster(nodeId)`. The Svelte `$effect` watching `clustersStore.selectedClusterId` drives all visual updates. No duplicate work in the click handler.

Same pattern for `handleParentNavigation`: `clustersStore.selectCluster(parentId)` (or `null` for overview).

### F16 — Highlight Survival on Async Rebuild

At the **end of `rebuildScene`** (after the scene has been re-populated), if a `focusedNodeId` exists, call `applyHighlight(focusedNodeId)`. This guarantees the cyan selection material survives the async stateFilter mutation that rebuilds every node mesh.

`applyHighlight` swaps both `material.color.setHex(HIGHLIGHT_COLOR)` AND `material.emissive.setHex(HIGHLIGHT_COLOR)` — without the emissive flip, the matte color reads cyan but the lit emission keeps emitting the original domain hex (desaturated mismatch under the directional light).

### F17 — One-Shot Auto-Focus

A module-level `_hasAutoFocused: boolean` guard ensures the bird's-eye-view "frame the largest domain" zoom runs **exactly once** during the component lifecycle. Subsequent rebuildScene calls (triggered by stateFilter mutations, taxonomy_changed events, etc.) do not re-trigger the auto-focus block. Without this guard, every async rebuild snapped the camera back to distance 60.

### F18 — Tactile Feedback on External Selection

When `clustersStore.selectedClusterId` changes via the sidebar (NOT a click on the canvas), the `$effect` watching it fires the same tactile feedback pipeline as a direct click. **All impact reactions wire through the beam's `onImpact` callback** (canon F7) so they synchronize to actual beam arrival — never synchronously with `beamPool.acquire(...)`:

```ts
beamPool.acquire(group, {
  color: envelopeColor,
  radius: Math.max(node.size * 0.04, 0.1),
  sustainMs: 800,
  onImpact: () => {
    clusterPhysics?.onBeamImpact(node.id, node.size);              // F9 ripple + accretion
    envelopePool?.acquire(group, node.size, envelopeShape, color); // F19 plasma engulfment
    flashEmissive(node.id);                                        // F19 emissive flash
  },
}, camera);
```

### F19 — Envelopement Burst at Beam Impact

Layered impact effect — at the moment the F7 beam visually arrives at the cluster, the node briefly engulfs in a plasma "skin" that smoothly dissipates to reveal the base node. Combined with the per-node `MeshStandardMaterial` emissive flash, the node reads as both internally energized AND externally engulfed.

**EnvelopePool (`EnvelopePool.ts`):**
- 10 pre-allocated `Envelope` instances with per-instance `ShaderMaterial` (own uniforms per envelope, mirrors `BeamPool` capacity)
- Pool's own `THREE.Group` added to the renderer scene — envelopes are **NOT** parented to target node groups, so a `rebuildScene` cleanup of node groups mid-effect cannot crash an active envelope. World position is copied from the target each frame.
- **Geometry:** pool-instance-owned `IcosahedronGeometry(1, 2)` and `DodecahedronGeometry(1, 2)` shared singletons; `acquire()` swaps the mesh's geometry to match the target's node shape (cluster vs. domain). Same parameters as the cluster + domain fill geometries declared in `SemanticTopology.svelte` `rebuildScene()` (`clusterFillGeo = new THREE.IcosahedronGeometry(1, 2)` + `domainFillGeo = new THREE.DodecahedronGeometry(1, 2)`) so the envelope shape exactly matches the node fill it wraps.
- **Material:** `ShaderMaterial` with vertex/fragment from `EnvelopeShader.ts` — adapts the F7 multi-wave fluid + fresnel rim glow pattern for closed surfaces (no muzzle flash, no length-wise smoothFade). `AdditiveBlending`, `depthWrite: false`, `transparent: true`, `side: FrontSide`.
- **State machine:** `idle → attack (220ms) → hold (180ms) → decay (580ms) → idle`. Total active duration **980ms**, synchronizes with the beam sustain window. Cubic ease-out for both attack and decay phases. The 220ms attack (was 120ms) + 580ms decay (was 500ms) replace earlier values that combined with an instant-jump emissive flash to produce a hard "thud" reading at impact.
- **Peak swell:** `PEAK_SWELL = 1.18` × `node.size` — visible plasma skin sits just outside the cluster silhouette without overlapping neighbors.
- **Color:** target cluster's `domain hex` (same as the beam — preserves the canon's "data has the node's identity" reading).
- **Re-acquire semantics:** re-acquiring an envelope on a node already enveloped reuses that instance and resets its state to `attack` — prevents double-stacked envelopes on rapid re-clicks.
- **Missing-target detection:** if `targetGroup` was attached at acquire time and has since lost its parent (e.g., `rebuildScene` disposed the cluster group), the envelope terminates gracefully on the next `update()` tick.
- *Source: `EnvelopePool.ts`, `EnvelopeShader.ts`*

**Emissive Flash (`SemanticTopology.svelte` `flashEmissive` + `_tickFlashStates`):**
- **Per-node `MeshStandardMaterial.emissiveIntensity` burst** at impact — internal glow that complements the additive plasma envelope.
- **Lifecycle constants:** `FLASH_PEAK_MULTIPLIER = 4` (4× baseline), `FLASH_ATTACK_MS = 80` (cubic ease-out RAMP from baseline up to peak — replaces an earlier instant jump that read as a hard "thud" alongside the envelope swell), `FLASH_DECAY_MS = 280` (cubic ease-out back to baseline). Total: 360ms. `flashEmissive()` only stamps the start time + captures baseline; the per-frame tick handles every interpolation.
- **Baseline-capture pattern:** rapid re-fires during an active flash REUSE the prior `baselineEmissive` — preventing emissiveIntensity from locking at peak² (4×4 = 16×) when a re-click reads the inflated live value as the "baseline".
- **State map:** `_flashStates: Map<string, { startTime: number; baselineEmissive: number }>` — tiny in steady state (only nodes recently impacted).
- **Cleanup:** `_removeFlashUpdate?.()` invoked BEFORE `_flashStates.clear()` (so a late-firing tick can't read a half-cleared map); each active flash's baseline is restored to the underlying material before the map is cleared (so a remount doesn't inherit inflated emissive).

**Causal-ordering invariant (with F7):** every F19 reaction fires from the beam's `onImpact` callback. Synchronous calls inside the click `$effect` (and the entrance + post-growth burst sites) are forbidden — the beam takes ~700ms (`FIRING_MS`) to travel, so a synchronous ripple/envelope/flash would precede beam arrival.

## Per-Frame Allocation Budget

Zero `new THREE.Vector3()` / `Color()` / `Quaternion()` / `Matrix4()` inside per-frame callbacks. With 50+ clusters at 60fps, allocating one Vector3 per cluster per frame produces ~3000 allocs/sec — visible jank on integrated GPUs.

**Canonical scratch table** (declare at module top of `SemanticTopology.svelte`):
```ts
const _scratchVec3a = new THREE.Vector3();
const _scratchQuat = new THREE.Quaternion();
const _scratchColor = new THREE.Color();
const Z_AXIS = new THREE.Vector3(0, 0, 1);
```

**Borrow rules:**
- `_scratchVec3a` / `_scratchQuat` / `_scratchColor` may be mutated by any callback as a transient. Treat the value as **invalid after the same callback returns** — no caching.
- `Z_AXIS` is read-only. Never `.set()` or `.copy()` it. Used by `rotateOnAxis(Z_AXIS, ...)` callers.

**Anti-pattern → replacement:**

| Per-frame anti-pattern | Replacement |
|---|---|
| `mesh.scale.lerp(new THREE.Vector3(s, s, s), 0.1)` | `mesh.scale.lerp(_scratchVec3a.set(s, s, s), 0.1)` |
| `obj.rotateOnAxis(new THREE.Vector3(0, 0, 1), 0.02)` | `obj.rotateOnAxis(Z_AXIS, 0.02)` |
| `target = a.clone().add(b)` | `target.copy(a).add(b)` |
| `new THREE.Color().setHSL(...)` per frame | `_scratchColor.setHSL(...)` |

**Test-time enforcement:** `perf-budget.test.ts` wraps `THREE.Vector3` / `Color` / `Quaternion` / `Matrix4` constructors via `vi.mock('three', importOriginal)` selective re-export, runs `BeamPool.update` and `ClusterPhysics.update` for 100 simulated frames, asserts zero allocations.

## Disposal Contract

Every GPU resource reaches a `dispose()` call from cleanup.

**Mandatory disposal targets:**

| Resource | Disposal |
|---|---|
| `BufferGeometry` | `geometry.dispose()` |
| `Material` (any subclass) | `material.dispose()` |
| `Texture` (incl. `CanvasTexture`) | `texture.dispose()` |
| `WebGLRenderTarget` | `renderTarget.dispose()` |
| `EffectComposer` | `composer.dispose()` + each `composer.passes[i].dispose()` |
| `Light.shadow.map` (DirectionalLight, SpotLight, PointLight) | `obj.shadow?.map?.dispose()` in `scene.traverse` |
| `WebGLRenderer` | `renderer.dispose()` + `renderer.forceContextLoss()` |
| Animation cancellers | invoke each `_remove*()` returned by `addAnimationCallback` |

**Required cancellers in cleanup return:**

| Canceller | Purpose |
|---|---|
| `removeBeamUpdate` | `BeamPool.update` per-frame |
| `_removeRingLodUpdate` | LOD-attenuated readiness ring opacity |
| `_removeFormationAnim?.()` | Formation animation lerp toward settled positions |
| `_removeDomainRotation?.()` | Domain group y-rotation (~50s/rev) |
| `_removeReadinessBillboard?.()` | Per-frame `lookAt(camera.position)` on rings |
| `_removeEdgeAnim?.()` | Edge shader `uTime` pulse |
| `_removeDustAnim?.()` | Neural Dust slow rotation |
| `_breathingAnim?.()` | Per-frame organic breathing oscillation |
| `_removeEnvelopeUpdate?.()` | F19 plasma envelope state-machine tick |
| `_removeFlashUpdate?.()` | F19 emissive flash per-frame restore |

**Required pool drains in cleanup:**

| Pool | Drain |
|---|---|
| `_readinessRings` | `for (entry of values) disposeRingEntry(entry)` then `_readinessRings.clear()` |
| `_templateRingPool` (formerly `_haloPool`) | Hide each, push to `_freeTemplateRings`. Pool array retained as high-water mark across remounts. |
| `_freeTemplateRings` | `length = 0` |
| `_templateRingById` | `clear()` |

**globalThis hygiene:**
- `__semTopGlowTexture` — disposed and set to `undefined` on unmount
- `__semTopTemplateRingPool` (test-only) — gated by `import.meta.env.MODE === 'test'`
- `__semTopLastScene` (test-only) — gated by mock setup, not present in production builds

## Audit Checklist

Run through each numbered feature. **The implementation passes if every line below is checked**.

### Visual canon
- [ ] **F1**: cluster fill = `MeshStandardMaterial` with `roughness: 0.6`, `metalness: 0`, `emissive` = domain hex, `emissiveIntensity` data-driven `[0.6, 1.4]`
- [ ] **F1**: cluster mesh `castShadow = true` AND `receiveShadow = true`
- [ ] **F2**: domain anchor vertex points = `PointsMaterial` with radial-gradient `__semTopGlowTexture` map, `AdditiveBlending`, `size: 0.35`
- [ ] **F2**: JSDOM stability — `if (!ctx) globalThis.__semTopGlowTexture = undefined` fallback
- [ ] **F3**: template ring = `RingGeometry(1.25, 1.35, 64)`, `MeshBasicMaterial`, color `0x00e5ff`, opacity `0.35`, `DoubleSide`
- [ ] **F3**: template ring pool = `_templateRingPool` with `INITIAL=50` / `GROW=50` / `MAX=500`
- [ ] **F3**: hover behavior — opacity oscillates, `rotation.z` spins
- [ ] **F4**: readiness ring uses `MeshBasicMaterial` with `depthWrite: false` AND `side: DoubleSide`
- [ ] **F4**: per-frame billboard via `_removeReadinessBillboard`
- [ ] **F4**: LOD-attenuated opacity (far/mid/near = 0.4/0.7/1.0)
- [ ] **F5**: hierarchical edges use `ShaderMaterial` with `uTime` uniform driven by `_removeEdgeAnim`
- [ ] **F5**: catenary sag = `0.15 * distance`
- [ ] **F6**: similarity edges use `LineDashedMaterial`; injection edges use `LineBasicMaterial`; both visibility-toggled via `clustersStore.showSimilarityEdges` / `showInjectionEdges`
- [ ] **F7**: BeamPool of 10 reusable `PlasmaBeam` instances; FPS-weapon NDC origin
- [ ] **F8**: `_breathingAnim` per-frame callback updates every cluster mesh + ring
- [ ] **F8**: hover amplifies breathing to `±12%`
- [ ] **F9**: ClusterPhysics integration uses `k=120`, `d=12`, `dt clamp=0.1`, `velocityFloor=1e-4`
- [ ] **F9**: snap-to-target floor (prevents infinite micro-oscillation)
- [ ] **F10**: Neural Dust = 3000-particle `Points` cloud, `0x88ccff`, additive blending, slow XY rotation
- [ ] **F11**: 3 lights (Ambient 0.3, Directional 0.7 at (5,10,5), Hemisphere 0.2)
- [ ] **F12**: `shadowMap.enabled = true`, `type = PCFShadowMap`, `mapSize 1024×1024`
- [ ] **F13**: `EffectComposer` with `RenderPass` + `UnrealBloomPass(1.5, 0.4, 0.85)` + `FilmPass(0.35, false)`
- [ ] **F13**: `composer.render()` not `renderer.render()`; `composer.setSize` on resize

### Interaction canon
- [ ] **F14**: `focusOn` with adaptive distance default — keeps current zoom when `distance` omitted, clamp `[5, 25]`
- [ ] **F14**: zero-length direction fallback to `+Z`
- [ ] **F14**: `_checkLod()` called per-frame inside the focus animate loop
- [ ] **F15**: `handleNodeClick` only calls `clustersStore.selectCluster(nodeId)` — no duplicate visual logic
- [ ] **F16**: `applyHighlight(focusedNodeId)` at end of `rebuildScene`
- [ ] **F16**: `applyHighlight` flips both `color` AND `emissive`
- [ ] **F17**: `_hasAutoFocused` guard — bird's-eye-view zoom runs exactly once per lifecycle
- [ ] **F18**: external selection wraps `clusterPhysics.onBeamImpact` (and the F19 envelope + flash) inside the `beamPool.acquire(...)` config's `onImpact` callback — never synchronously alongside `acquire()`
- [ ] **F19**: `EnvelopePool` constructed in `onMount`; envelope geometry shape literals match `node.state === 'domain' ? 'domain' : 'cluster'`
- [ ] **F19**: `flashEmissive` uses baseline-capture pattern — rapid re-fires reuse prior `baselineEmissive`
- [ ] **F19**: causal-ordering invariant — every `beamPool.acquire(...)` site (click `$effect`, entrance materialization burst, post-growth burst) wires `clusterPhysics.onBeamImpact` (and envelope/flash) inside `onImpact: () =>`, never synchronously alongside `acquire()`

### Performance + lifecycle
- [ ] Module-level scratch table declared (`_scratchVec3a`, `_scratchQuat`, `_scratchColor`, `Z_AXIS`)
- [ ] `_breathingAnim` borrows `_scratchVec3a` for `mesh.scale.lerp` (not `new THREE.Vector3()`)
- [ ] All 10 cancellers wired in cleanup return (Disposal Contract list above — includes F19 `_removeEnvelopeUpdate?.()` + `_removeFlashUpdate?.()`)
- [ ] `envelopePool?.dispose()` invoked + reference nulled in cleanup return
- [ ] `_flashStates.clear()` happens AFTER `_removeFlashUpdate?.()` (so a half-cleared map can't be read by a late-firing tick); each active flash's baseline restored before the map is cleared
- [ ] All disposables drained (geometries, materials, textures, lights' shadow maps)
- [ ] `composer.dispose()` + each pass disposed
- [ ] `renderer.dispose()` + `renderer.forceContextLoss()`
- [ ] Pool retention: `_templateRingPool` array preserved across unmount (high-water mark)

If every box is checked: **PASS.** The implementation matches the canon.

## Acceptance for changes

A change to the 3D Pattern Graph is acceptable if **either**:

1. It implements one of the canonical features above more correctly (e.g., fixes a per-frame allocation, tightens parameters to match the spec).
2. It adds a new canonical feature, in which case **this document is updated** alongside the code so the audit checklist stays comprehensive.

A change is **NOT** acceptable if it removes a canonical feature without an explicit update to this document AND user approval. The 3D Pattern Graph is its own medium — it does not bend to 2D-UI rules absent a user-driven redirection.
