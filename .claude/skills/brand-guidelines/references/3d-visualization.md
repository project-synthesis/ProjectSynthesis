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
| `flash` | Brief emission lift on the per-node `MeshStandardMaterial.emissiveIntensity` at beam impact — 120ms cubic ease-out attack from `startIntensity = max(currentEmissive, baseline)` to `baseline + 1.6` (additive `GLOW_PEAK_DELTA`, not multiplicative), 580ms hold at peak, 680ms cubic ease-out decay back to baseline. Total active window: 1380ms (matches plasma envelope total active duration). Owned by `FlashController.ts` post-Sub-project E; fires from the beam's `onImpact` callback so it synchronizes with the plasma envelopement and cluster-physics ripple. |

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
  - `emissive:` — clusters use coherence-warmed color (see below), domain anchors use raw `node.color`
  - `emissiveIntensity` — **data-driven**, compound signal:
    - Base (member count): `0.6 + 0.8 * clamp((memberCount - 1) / 49, 0, 1)` → `[0.6, 1.4]`
    - Domain anchor: `0.4` (recessed — context, not foreground)
    - Coherence boost (clusters only): `× (1.0 + 0.1 * coherence)` — hotter clusters radiate more
    - Score boost (clusters only): `× (0.85 + 0.15 * clamp(avgScore / 10, 0, 1))` — high-scoring clusters bloom brighter
    - Hovered cluster: multiplied by `1.0 + 0.4 * hoverIntensity` (transient lift)
  - **Coherence-driven color temperature** (chromatic encoding axiom): cluster `emissive` color lerps toward white by `coherence * 0.03` — high-coherence clusters read as "hotter plasma" (shifted toward white/cyan), low-coherence clusters stay at domain base hue. Domain anchors do not participate.
  - `roughness: 0.6` (matte)
  - `metalness: 0.0`
  - `transparent: true`, `opacity: node.opacity * 0.9`
- **Mesh flags:** `castShadow = true`, `receiveShadow = true` (self-shadowing under directional light)
- **Scale:** initial `mesh.scale.setScalar(node.size)`; per-frame breathing modulates this (see F8)
- *Source: `frontend/src/lib/components/taxonomy/builders/ClusterBuilder.ts` (cluster fill + ripple wireframe) + `frontend/src/lib/components/taxonomy/builders/DomainBuilder.ts` (domain dodecahedron fill — emissive recipe lives in the structural-emissive helper). Sub-project D extracted the inline construction blocks from `SemanticTopology.svelte`.*

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
- *Source: `frontend/src/lib/components/taxonomy/builders/DomainBuilder.ts` (Sub-project D — owns the lazy `__semTopGlowTexture` build + the `PointsMaterial` + `Points` mesh construction for every structural node).*

### F3 — Template Indicator Ring (`halo`)

- **Geometry:** `RingGeometry(1.25, 1.35, 64)` — 64-segment smooth ring
- **Material:** `MeshBasicMaterial` (banner overlay, not a 3D shaded sphere)
  - `color: 0x00e5ff` (neon cyan)
  - `transparent: true`, `opacity: 0.35` (default)
  - `side: THREE.DoubleSide`
- **Pool:** growable mesh pool — `TEMPLATE_RING_POOL_INITIAL=50` / `GROW_CHUNK=50` / `MAX=500`. High-water mark retained across rebuilds.
- **Hover behavior:** `rotation.z += 0.02` per frame (spin), `material.opacity` oscillates `0.20–0.50` via `0.35 + sin(time * 10) * 0.15`
- **Position sync:** position written every formation-animation frame (avoids the rings-pinned-at-origin bug)
- *Source: `frontend/src/lib/components/taxonomy/builders/RingBuilder.ts` (Sub-project D — owns the template ring pool infrastructure + `_syncTemplateRings` + getters `getTemplateRing`. Pool constants `TEMPLATE_RING_POOL_INITIAL / GROW_CHUNK / MAX` live as module-scope constants at the top of the file). Renamed in cycle 2 from `_ensureHaloPool` / `_syncHalos`. Both old + new identifier names are acceptable per Canon Vocabulary.*

### F4 — Readiness Ring (per-domain composite tier indicator)

- **Geometry:** `RingGeometry(radius, radius + 0.05, 64)` where `radius = node.size * 1.25`
- **Material:** `MeshBasicMaterial`
  - `color: readinessTierColor(tier)`
  - `transparent: true`, `opacity: node.opacity * READINESS_RING_OPACITY_FACTOR (0.9)`
  - `depthWrite: false` (sits over geometry)
  - `side: THREE.DoubleSide` (visible from below the canopy view)
- **Per-frame billboard:** `_removeReadinessBillboard` callback iterates rings, calls `entry.mesh.lookAt(camera.position)` so the ring stays orthogonal to the view
- **LOD-attenuated opacity:** far `0.4`, mid `0.7`, near `1.0` (multiplied with base)
- *Source: `frontend/src/lib/components/taxonomy/builders/RingBuilder.ts` (Sub-project D — owns the readiness ring lifecycle: `_syncReadinessRings`, `_buildReadinessRingEntry`, `_pruneReadinessRings`, `getReadinessRing(id)` / `readinessRingCount()` / `readinessRingIds()` accessors). The per-frame billboard + LOD-attenuation callbacks remain in `SemanticTopology.svelte` (orchestrator-owned per-rebuild registrations).*

### F5 — Hierarchical Edge (parent → children fan-out)

- **Geometry:** merged `BufferGeometry` of catenary curves between parent and each child
- **Material:** `ShaderMaterial` with `EDGE_DEPTH_VERTEX` / `EDGE_DEPTH_FRAGMENT`
  - Uniforms: `{ uColor, uOpacity, uTime }`
  - `transparent: true`, `depthWrite: false`
- **Per-frame pulse:** `_removeEdgeAnim` callback updates `uTime` value on every registered edge's uniforms — drives shader pulse (data-flow signal)
- **Color:** parent's domain hex (`parentNode.color`)
- **Catenary sag:** `0.15 * distance` (15% of cluster-to-cluster distance)
- **Domain structural edge shader:** domain dodecahedron `EdgesGeometry` uses `DOMAIN_EDGE_VERTEX` / `DOMAIN_EDGE_FRAGMENT` (`DomainEdgeShader.ts`) — half-frequency "slow heartbeat" pulse that differentiates the parent container's rhythm from the faster child data-flow signal. Higher baseline opacity (0.4 vs 0.3) so the domain container never fully dims between pulses. Moderate HDR boost (0.5 vs 0.8) so domains don't outshine data-carrying children. Shared `uTime` uniform keeps both systems phase-coherent.
- *Source: `frontend/src/lib/components/taxonomy/builders/EdgeBuilder.ts` (hierarchical + similarity + injection edge construction; owns `buildMergedCurveGeometry` + `buildCurvePositions` private copies) + `frontend/src/lib/components/taxonomy/builders/DomainBuilder.ts` (domain-edge branch — pushes domain-edge uniforms into `ctx.edgeUniforms` so the F5 per-frame uTime tick covers both child + parent rhythms) + `EdgeShader.ts` + `DomainEdgeShader.ts`. `SemanticTopology.svelte` retains a copy of `buildMergedCurveGeometry` + `buildCurvePositions` for the formation-animation post-completion rebuild path.*

### F6 — Similarity / Injection Edge

- **Material:** `LineDashedMaterial` (similarity, dashed) or `LineBasicMaterial` (injection, solid)
- **Color:** `SIMILARITY_EDGE_COLOR` constant or domain hex
- **Toggle-driven visibility** via `clustersStore.showSimilarityEdges` / `showInjectionEdges`
- *Source: `frontend/src/lib/components/taxonomy/builders/EdgeBuilder.ts` `_buildSecondaryEdgeGroup` shared builder (Sub-project D — extracted from `SemanticTopology.svelte`'s `buildEdgeGroup` helper). `EdgeOpacityResolvers` constructor dep wires `clustersStore.showSimilarityEdges` / `showInjectionEdges` into the build-time `.visible` flag; live runtime toggles route through `edgeBuilder.getSimilarityGroup()` / `getInjectionGroup()` accessors.*

### F7 — Plasma Beam Tactile Feedback

- **Phase:** impact (within-phase order STRICT — see Animation Tick Ordering).
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
- **Canonical trigger:** `ImpactCoordinator.fire({ trigger, node, group })` (`frontend/src/lib/components/taxonomy/ImpactCoordinator.ts`) is the single entry point for all four impact-trigger sites (entrance burst, post-growth burst, optimization event, click selection). Per-trigger parameters live in `TRIGGER_PRESETS` keyed by `Trigger` ('entrance' | 'post-growth' | 'optimization' | 'click'). The coordinator's `fire()` body constructs the `BeamConfig` (including `onImpact`) and calls `beamPool.acquire(...)` exactly once — every impact reaction (F9 ripple + accretion, F19 envelope, F19 flash) appears inside the coordinator's `onImpact` body. The causal-ordering invariant is enforced at source level by source-grep test #6 in `frontend/src/lib/components/taxonomy/cleanup-contract.test.ts` — zero matches for `clusterPhysics.onBeamImpact(`, `envelopePool.acquire(`, `flashEmissive(` outside the coordinator (modulo the `function flashEmissive(` declaration carveout, which stays in `SemanticTopology.svelte` pending Sub-project E).
- *Source: `ImpactCoordinator.ts`, `BeamPool.ts`, `PlasmaBeam.ts`, `BeamShader.ts`*

### F8 — Organic Breathing Oscillation

- **Phase:** breathing (after physics phase to read spring-set baseline; after impact phase's flash to avoid emissive overwrites — see Animation Tick Ordering).
- **Per-frame callback:** `_breathingAnim`
- **Time accumulator:** `_breathingTime += 0.016` per frame (~60fps)
- **Per-node phase offset:** `_nodePhaseOffsets: Map<string, number>` populated during `rebuildScene` via a deterministic string hash of the node ID mapped to `[0, 2π]`. Each cluster oscillates at the same frequency but with a unique phase shift, so the colony reads as organic rather than a synchronized grid.
- **Base:** `scaleBase = sin((_breathingTime + phaseOffset) * 1.5) * 0.02 + 1.0` → `±2%` oscillation
- **Hover amplification:** `targetScaleMultiplier = isHovered ? scaleBase * 1.1 : scaleBase` → up to `±12%` when hovered
- **Apply to:** every cluster mesh (`mesh.scale.lerp(_scratchVec3a.set(s, s, s), 0.1)`), the parent group's children at the same scale, the readiness ring (`scale.setScalar(targetScaleMultiplier)`), the template ring
- **Allocation budget:** the `_scratchVec3a` borrow in `mesh.scale.lerp(...)` is mandatory — the per-frame `new THREE.Vector3()` form is banned (see "Per-Frame Allocation Budget" below)
- *Source: `SemanticTopology.svelte` `_breathingAnim` callback (Sub-project D — extracted to the `_advanceBreathing` private helper for orchestrator LOC compression; the per-frame callback registration still lives in `rebuildScene`). Phase-offset hash formula lives in `frontend/src/lib/components/taxonomy/builders/ClusterBuilder.ts` + `DomainBuilder.ts` — both builders write `ctx.nodePhaseOffsets[node.id]` using the identical char-code sum hash so the orchestrator's module-level `_nodePhaseOffsets` is populated consistently across cluster + structural nodes.*

### F9 — Spring Physics on Cluster Scale (Beam Impact Accretion)

- **Phase:** physics (after impact phase so onImpact's targetScale changes integrate same-frame — see Animation Tick Ordering).
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

- **Phase:** ambient (decoupled from interactive pipeline — see Animation Tick Ordering).
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
- *Source: `frontend/src/lib/components/taxonomy/builders/DustBuilder.ts` (Sub-project D — owns the lazy `_dustPoints` construction + T3.1 vertex-color tinting; exposes `dustPoints()` accessor). The `_removeDustAnim` per-frame rotation handler stays in `SemanticTopology.svelte` and reads `dustBuilder.dustPoints()` to advance rotation.*

### F11 — Lighting Setup

| Light | Constructor | Position | Notes |
|---|---|---|---|
| `AmbientLight` | `(0xffffff, 0.3)` | n/a | Baseline scene fill |
| `DirectionalLight` | `(0xffffff, 0.7)` | `(5, 10, 5)` | Primary key. `castShadow=true`. `shadow.mapSize.set(1024, 1024)` |
| `HemisphereLight` | `(0x1a1a2e, 0x06060c, 0.2)` | n/a | Sky/ground organic. Sky matches `DEFAULT_BG = 0x06060c` |
| `PointLight` | optional | hero focal | **Permitted** as a focal accent on the user-selected cluster only. Never as ambient atmosphere |
| `SpotLight` | — | — | Banned. Cone falloff contradicts signal-over-noise — it emphasizes empty space rather than a data-bearing surface. (`halo` in this document is reserved for the F3 template indicator ring.) |

**Persistence:** each light sets `userData.persistent = true` immediately after construction (per Persistence Contract) so they survive `rebuildScene` cycles. Pre-fix lights were orphaned every rebuild, breaking shadow map (F12) and matte cluster fill (F1 color channel) — see lifecycle-hardening spec §1.

### F12 — Shadow Map

- `renderer.shadowMap.enabled = true`
- `renderer.shadowMap.type = THREE.PCFShadowMap` (canonical; `PCFSoftShadowMap` was deprecated in three@0.170+)
- `DirectionalLight.shadow.mapSize.set(1024, 1024)` (2048 for hero scenes if perf budget allows)
- Cluster meshes both `castShadow` AND `receiveShadow` for self-shadowing
- *Source: `TopologyRenderer.ts` constructor + cluster fill block*

**Lights persist across rebuilds:** the 3 lights set `userData.persistent = true` at construction (per F11 + Persistence Contract) so `cleanupScene` preserves them. Pre-fix `DirectionalLight.shadow.map` was created on first mount and orphaned on every subsequent `rebuildScene`, leaving self-shadowing non-functional.

### F13 — Post-Processing Pipeline

The 3D Pattern Graph **uses post-processing**. Render via `composer.render()` not `renderer.render()`.

| Pass | Permitted? | Parameters | Reason |
|---|---|---|---|
| `RenderPass` | **Required** | scene + camera | Foundation for the composer chain |
| `UnrealBloomPass` | **Yes** | `(resolution, strength: 1.5, radius: 0.4, threshold: 0.85)` | Amplifies emissive surfaces — the canonical "alive" feeling |
| `FilmPass` | **Yes** | `(intensity: 0.35, grayscale: false)` | Cinematic grain — texture without noise. `intensity ≤ 0.4` keeps it subtle |
| `SMAAPass` / `FXAAPass` | **Yes** | default | Anti-aliasing — supports edge sharpness. `SMAAPass` is the canonical choice (placed after `FilmPass`). |
| `ToneMappingPass` | **Yes (rare)** | when doing PBR with HDR colors | Default `THREE.NoToneMapping` is fine for non-HDR |
| `GodRaysPass` / `GlitchPass` / `DotScreenPass` / `RGBShiftPass` | **No** | — | Aesthetic-only or decorative; no data signal. The brand-compliance test bans imports of all four. |

**Composer setup pattern:**
```ts
this.composer = new EffectComposer(this.renderer);
this.composer.addPass(new RenderPass(this.scene, this.camera));
this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(w, h), 1.5, 0.4, 0.85));
this.composer.addPass(new FilmPass(0.35, false));
this.composer.addPass(new SMAAPass(w, h));
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

At the **end of `rebuildScene`** (after the scene has been re-populated), `selectionController.afterRebuild()` is invoked deterministically by the orchestrator. This is the canonical highlight-survival mechanism — it replaces the pre-Sub-project-E "Highlight Survival Hack" (an inline `applyHighlight(focusedNodeId)` line tacked on the end of `rebuildScene`) with an explicit state-machine call that the controller owns.

`SelectionController.afterRebuild()` re-runs `_applyHighlight(this._selectedId)` against the freshly-rebuilt mesh map: the old `_highlightedId` / `_highlightedColor` mesh refs are nulled (they pointed at the disposed mesh from the previous scene), then the dual color + emissive swap is re-applied to the new mesh. The dual swap is verbatim `mat.color.setHex(HIGHLIGHT_COLOR)` + `mat.emissive.setHex(HIGHLIGHT_COLOR)` — without the emissive flip, the matte color reads cyan but the lit emission keeps emitting the original domain hex (desaturated mismatch under the directional light). Source: `SelectionController.ts:_applyHighlight`.

The `_highlightedColor` snapshot (the previous mesh's `color.getHex()` captured at highlight time) survives the rebuild via SC's private `_highlightedColor` field — it is restored on `clearSelection()` (or any selection-changed-via-`select(nodeId)` transition) by SC's `_clearHighlight`, which writes `mat.color.setHex(this._highlightedColor)` + `mat.emissive.setHex(this._highlightedColor)` against whichever mesh the live `_highlightedId` points at. Source: `SelectionController.ts:_clearHighlight`.

### F17 — One-Shot Auto-Focus

A module-level `_hasAutoFocused: boolean` guard ensures the bird's-eye-view "frame the largest domain" zoom runs **exactly once** during the component lifecycle. Subsequent rebuildScene calls (triggered by stateFilter mutations, taxonomy_changed events, etc.) do not re-trigger the auto-focus block. Without this guard, every async rebuild snapped the camera back to distance 60.

### F18 — Tactile Feedback on External Selection

When `clustersStore.selectedClusterId` changes via the sidebar (NOT a click on the canvas), the `$effect` watching it fires the same tactile feedback pipeline as a direct click — both route through `impactCoordinator.fire({ trigger: 'click', node, group })` (canon F7 canonical trigger). The coordinator's `onImpact` body wires the F9 ripple, F19 envelope, and F19 flash — see `frontend/src/lib/components/taxonomy/ImpactCoordinator.ts`. After the beam impact, the selected node's id is tracked in `SelectionController`'s internal engulfed set; the canonical read is `SelectionController.isEngulfed(id)` (Sub-project E migrated ownership from `ImpactCoordinator._selectionEngulfed` to SC — IC no longer holds engulfed state). The breathing handler in `SemanticTopology.svelte` reads this via a no-op middle branch (`else if (selectionController?.isEngulfed(nodeId) && isSelected) { /* no-op */ }`) so SC's impact-phase `_tick` owns the post-engulfment idle ambient pulse (T3.4) without being overwritten by the bare-else `mat.emissiveIntensity = baseEmissive` reset.

### F19 — Envelopement Burst at Beam Impact

**Phase:** impact (envelope + flash both registered in impact phase after beam — see Animation Tick Ordering).

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

**Persistence:** `EnvelopePool.group` sets `userData.persistent = true` in the constructor (per Persistence Contract). The pre-fix manual `scene.remove(envelopePool.group)` / `scene.add(envelopePool.group)` save/restore in `rebuildScene` is replaced by `cleanupScene`'s flag-aware detach/reattach.

**Emissive Flash (`FlashController.ts`, post-Sub-project E):**
- **Per-node `MeshStandardMaterial.emissiveIntensity` burst** at impact — internal glow that complements the additive plasma envelope.
- **Lifecycle constants:** `GLOW_PEAK_DELTA = 1.6` (ADDITIVE — `peak = baselineEmissive + 1.6`, not multiplicative), `GLOW_ATTACK_MS = 120` (cubic ease-out RAMP from `startIntensity` up to peak — replaces an earlier instant jump that read as a hard "thud" alongside the envelope swell), `GLOW_HOLD_MS = 580` (hold at peak), `GLOW_DECAY_MS = 680` (cubic ease-out back to baseline), `GLOW_TOTAL_MS = 1380` (= 120 + 580 + 680; matches the plasma envelope's total active duration). `FlashController.flash()` only stamps the start time + captures baseline + `startIntensity` + `domainEmissive`; the per-frame `_tick` handles every interpolation.
- **Additive (not multiplicative) rationale:** +1.6 from baseline keeps high-baseline clusters out of the bloom whiteout zone — a 4× multiplier on a high-baseline (1.4) cluster would reach 5.6, deep into whiteout; +1.6 gives a strong surge on domains (0.4→2.0) and a decisive punch on clusters (0.6→2.2) without overdriving the bloom pass.
- **Baseline-capture pattern (F19 invariant):** rapid re-fires during an active flash REUSE the prior `baselineEmissive` AND the prior `startIntensity` — preventing emissiveIntensity from locking at peak (re-click reads the inflated live value as the "baseline") and preserving the continuous attack ramp from the in-flight intensity rather than re-jumping down to baseline (B6 mid-flash continuity).
- **State map:** `FlashController.ts` field `_states: Map<string, FlashState>` — tiny in steady state (only nodes recently impacted). The `FlashState` interface shape is preserved verbatim from the pre-Sub-project-E inline declaration: `{ startTime: number; baselineEmissive: number; startIntensity: number; domainEmissive: THREE.Color }`. Migrated from the inline `_flashStates` Map declaration (pre-Sub-project E) into the FlashController class field.
- **Cleanup:** `SelectionController.dispose() → FlashController.dispose()` (iterates active flashes; restores `mat.emissiveIntensity = baselineEmissive` per F19 invariant; emissive color decided by live `_highlightedId === nodeId` check via FC's `isHighlighted` dep — `HIGHLIGHT_COLOR` if still selected, `state.domainEmissive` otherwise). `selectionController?.dispose()` runs as part of the cleanup-return canceller list before the AnimationCoordinator tears down, so a late-firing tick cannot read a half-cleared state map.

**Causal-ordering invariant (with F7):** every F19 reaction fires from the beam's `onImpact` callback. Synchronous calls inside the click `$effect` (and the entrance + post-growth burst sites) are forbidden — the beam takes ~700ms (`FIRING_MS`) to travel, so a synchronous ripple/envelope/flash would precede beam arrival. **Canonical trigger:** `ImpactCoordinator.fire({ trigger, node, group })` owns every impact site. The envelope is acquired inside the coordinator's `onImpact` body with the raw `freshNode.size` (no floor) — the coordinator looks up the latest node by id from the scene-node map before passing it to `envelopePool.acquire(...)`, so the peak swell tracks any in-flight size changes from `clusterPhysics.onBeamImpact()` that fired earlier in the same impact-phase callback chain.

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

## Animation Tick Ordering

Per-frame animation work in the 3D Pattern Graph runs through a single
`AnimationCoordinator` instance (one per `SemanticTopology` mount). The
coordinator owns the single per-frame callback registered via
`renderer.addAnimationCallback(() => this._tick())` and dispatches to
handlers in a fixed 5-phase order:

| Order | Phase | Responsibility |
|---|---|---|
| 1 | `impact` | F7 beam.update → F19 envelope.update → F19 flash.update (within-phase order STRICT) |
| 2 | `physics` | F9 clusterPhysics.update |
| 3 | `breathing` | F8 organic breathing oscillation + camera-shake decay |
| 4 | `ambient` | F5 edge pulse, F10 dust drift, domain rotation, formation entrance (conditional) |
| 5 | `camera` | F4 readiness billboard `lookAt(camera.position)`, readiness LOD opacity sweep |

**Rationale.** `impact` runs first because `beam.update` fires `onImpact`
synchronously, mutating `clusterPhysics.targetScale` (F9), acquiring new
envelopes (F19), and stamping new flash states (F19). Within `impact`, beam
must run BEFORE envelope + flash so this frame's new state advances same-frame.
`physics` runs after `impact` so the new `targetScale` integrates on the
same frame the beam arrived (zero-latency spring response). `breathing`
modulates `mesh.scale` after physics writes the spring-set baseline; it also
reads `_flashStates` to skip emissive overwrites (without this read +
the run-after-flash order, breathing overwrites the F19 attack ramp — the
very regression this section's invariant exists to prevent). `ambient` runs after the interactive
pipeline because it's decoupled. `camera` runs last so billboard `lookAt`
+ LOD opacity reflect the latest mesh state for this frame before
`composer.render()`.

**Within-phase ordering.** Most phases run handlers in registration order
(FIFO). For `impact`, the strict beam → envelope → flash order is
load-bearing and pinned by source-grep test in `cleanup-contract.test.ts`.
Other phases tolerate registration-order changes without visible regression.

**Coordinator construction happens once in `SemanticTopology.svelte` onMount**,
after `renderer = new TopologyRenderer(canvas)` and before the first
`coordinator.register(...)` call (relative ordering only — no fixed line
offset). All onMount + rebuildScene per-frame work goes through
`coordinator.register(phase, handler)`; the return value is an unsubscribe
function (mirrors the original `addAnimationCallback` API for conditional
registrations like formation entrance + readiness billboard).

**Per-frame allocation budget.** The coordinator's `_tick` allocates zero
per-frame objects — `delta` is a number, `PHASE_ORDER` is a compile-time
constant readonly tuple, handler arrays are pre-allocated at construction.
See "Per-Frame Allocation Budget" above.

## Scene Builder Ownership

Sub-project D (v0.4.25) split the 774-line `rebuildScene` in
`SemanticTopology.svelte` into 5 single-responsibility builders living
under `frontend/src/lib/components/taxonomy/builders/`:

| Builder | Owns | Persistent flag |
|---|---|---|
| `ClusterBuilder.ts` | Per-cluster `THREE.Group` containers (Icosahedron fill + ripple wireframe + per-cluster `ShaderMaterial`). Writes `ctx.nodeMeshes` / `ctx.beamNodeGroups` / `ctx.clusterShaderMaterials` / `ctx.nodePhaseOffsets`. Registers cluster fill with `TopologyInteraction` via constructor dep. | none — per-cluster groups rebuild each cycle |
| `DomainBuilder.ts` | Per-domain `THREE.Group` containers (Dodecahedron fill + `EdgesGeometry` `LineSegments` + vertex glow `Points` — canon F2). Writes `ctx.nodeMeshes` / `ctx.beamNodeGroups` / `ctx.domainGroups` / `ctx.edgeUniforms` (domain-edge uniforms) / `ctx.nodePhaseOffsets`. Owns the canon F2 glow `CanvasTexture` lazy build. Registers domain fill with `TopologyInteraction`. | none — `userData.isStructural = true` preserved for selection code |
| `EdgeBuilder.ts` | 3 ephemeral sub-groups — `hierarchicalGroup` (catenary curves, canon F5), `similarityEdgeGroup` (dashed cluster-to-cluster), `injectionEdgeGroup` (solid domain-to-domain). Pushes hierarchical-bucket uniforms into `ctx.edgeUniforms`. Exposes `getSimilarityGroup()` / `getInjectionGroup()` accessors for the visibility-toggle `$effect`s. | none |
| `RingBuilder.ts` | Readiness rings (F4 — per-domain contour ring colored by composite tier, `DoubleSide`) + template rings (F3 — per-cluster `0x00e5ff` indicator with pool-managed lifecycle). Exposes `getReadinessRing(id)` / `getTemplateRing(id)` / `readinessRingCount()` / `readinessRingIds()` accessors for the per-frame breathing handler + LOD opacity callback. Re-publishes the `__semTopTemplateRingPool` dev hook each build for runtime template-ring pool tests. | `_readinessRingGroup` + `_templateRingGroup` set `userData.persistent = true` |
| `DustBuilder.ts` | 3000-point Neural Dust backdrop (canon F10) with T3.1 nearest-anchor vertex-color tinting. Exposes `dustPoints(): THREE.Points \| null` accessor for the ambient-rotation handler. | `_dustPoints` sets `userData.persistent = true` |

Build order is fixed in the orchestrator (post-migration `rebuildScene`
body): **cluster → domain → edge → ring → dust**. EdgeBuilder +
RingBuilder read `ctx.nodeMeshes` populated by ClusterBuilder +
DomainBuilder; DustBuilder is order-independent but conventionally last.
A source-grep contract test (`cleanup-contract.test.ts §5.2 #4`) pins
this exact order at the orchestrator body.

Shared per-rebuild state flows through a typed `BuilderContext`
interface (`builders/BuilderContext.ts`) — 7 maps + arrays passed
explicitly to every `build(data, scene, ctx)` call. Module-level state
(`nodeMeshes`, `_beamNodeGroups`, `_edgeUniforms`, `_nodePhaseOffsets`,
`_clusterShaderMaterials`) is **reassigned from ctx AFTER all builders
complete**; per-frame handlers read the module-level refs on the next
RAF tick — race-free per spec §4 acceptance.

The `ImpactCoordinator` physics handler reads
`ctx.clusterShaderMaterials.get(nodeId)` (via the module-level
`_clusterShaderMaterials` sync) instead of walking `group.children[1]`
to find the wire mesh — locks the architecture improvement at source
level. Integration test INT-6 (`builders/integration.test.ts`) pins
this wiring.

Cleanup contract: `cleanupScene` (Sub-project A) disposes ephemeral
children at the start of each rebuild; persistent groups survive via
`userData.persistent`. Builder `dispose()` methods release internal
state + detach persistent parents on component unmount (called from
`SemanticTopology.svelte`'s cleanup return between
`coordinator.dispose()` and pool disposes).

Per-frame accumulators (`_breathingTime`, `_edgeTime`, `_cameraShake`,
`_nodePhaseOffsets`) **remain at module scope** in
`SemanticTopology.svelte` — builders own per-rebuild ephemeral state,
not per-frame accumulator state.

## Persistence Contract

Any `THREE.Object3D` added as a direct child of `renderer.scene` that
must survive a `rebuildScene` cycle sets `obj.userData.persistent = true`
at construction. The `cleanupScene` helper in
`frontend/src/lib/components/taxonomy/scene-cleanup.ts` reads this flag —
flagged children are detached before the dispose traverse, ephemeral
children's geometries + materials are disposed, the scene is cleared,
and persistent children are reattached.

Use **dot-assignment** (`obj.userData.persistent = true`) to preserve
any other fields. Object-replacement (`obj.userData = { persistent: true }`)
clobbers existing tags like `isNeuralDust` (F10) and is banned by
`cleanup-contract.test.ts`.

Currently persistent (registered via dot-assignment at construction):
`BeamPool.group`, `EnvelopePool.group`, `TopologyLabels.group`,
`AmbientLight`, `DirectionalLight`, `HemisphereLight`, `_dustPoints`,
`_readinessRingGroup`, `_templateRingGroup`.

The persistence flag applies **only to direct children of**
`renderer.scene`. Children of persistent parent groups (e.g., individual
readiness rings inside `_readinessRingGroup`) follow their own owner's
disposal path.

The dispose traverse in `cleanupScene` covers `Mesh`, `LineSegments`,
`Line`, and `Points` — complementing (not duplicating) the renderer-level
dispose pattern in `TopologyRenderer.dispose()`, which handles `Mesh +
LineSegments + Points + Sprite + Light.shadow.map` on unmount. The two
paths cover every geometry-owning class used in this directory: the
helper adds `Line` (which the renderer omits) and intentionally omits
`Sprite` (owned by `labels.clear()`) and `Light.shadow.map` (lights are
persistent and live the component lifetime). The helper's coverage is
broader than the pre-refactor rebuildScene traverse, which covered only
`Mesh + LineSegments` and silently leaked the per-domain `PointsMaterial`
glow cores (F2's vertex energy cores) every rebuild.

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
| `impactCoordinator.dispose()` | **MUST run first** — cancels the F7→F19→F9 chain (4 trigger sites route through `impactCoordinator.fire(...)`) + the impact-phase T3.4 idle ambient pulse handler. Disposing first short-circuits any in-flight RAF-driven `_tick` before AnimationCoordinator tears down. Per Sub-project C spec acceptance #10. |
| `coordinator.dispose()` | Cancels all unconditional per-frame work via AnimationCoordinator (replaces removeBeamUpdate, _removeRingLodUpdate, _removeDomainRotation, _removeEdgeAnim, _removeDustAnim, _breathingAnim, _removeEnvelopeUpdate, _removeFlashUpdate); per Animation Tick Ordering section. |
| `_removeFormationAnim?.()` | Formation animation lerp toward settled positions (conditional during entrance) |
| `_removeReadinessBillboard?.()` | Per-frame `lookAt(camera.position)` on rings (conditional when `_readinessRings.size > 0`) |

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
- [ ] **F1**: cluster fill = `MeshStandardMaterial` with `roughness: 0.6`, `metalness: 0`, `emissiveIntensity` data-driven compound signal (member count + coherence + score)
- [ ] **F1**: cluster mesh `castShadow = true` AND `receiveShadow = true`
- [ ] **F1**: coherence-driven color temperature — cluster `emissive` lerps toward white by `coherence * 0.03`
- [ ] **F1**: score-driven emissive modulation — `avgScore` lifts emissiveIntensity via `0.85 + 0.15 * clamp(avgScore/10, 0, 1)` multiplier
- [ ] **F2**: domain anchor vertex points = `PointsMaterial` with radial-gradient `__semTopGlowTexture` map, `AdditiveBlending`, `size: 0.35`
- [ ] **F2**: JSDOM stability — `if (!ctx) globalThis.__semTopGlowTexture = undefined` fallback
- [ ] **F3**: template ring = `RingGeometry(1.25, 1.35, 64)`, `MeshBasicMaterial`, color `0x00e5ff`, opacity `0.35`, `DoubleSide`
- [ ] **F3**: template ring pool = `_templateRingPool` with `INITIAL=50` / `GROW=50` / `MAX=500`
- [ ] **F3**: hover behavior — opacity oscillates, `rotation.z` spins
- [ ] **F3**: entry/exit transitions — 400ms cubic ease-in on acquire, 300ms cubic ease-out on release
- [ ] **F4**: readiness ring uses `MeshBasicMaterial` with `depthWrite: false` AND `side: DoubleSide`
- [ ] **F4**: per-frame billboard via `_removeReadinessBillboard`
- [ ] **F4**: LOD-attenuated opacity (far/mid/near = 0.4/0.7/1.0)
- [ ] **F5**: hierarchical edges use `ShaderMaterial` with `uTime` uniform driven by `_removeEdgeAnim`
- [ ] **F5**: catenary sag = `0.15 * distance`
- [ ] **F5**: domain structural edges use `DomainEdgeShader` (`ShaderMaterial`) with shared `uTime`, half-frequency heartbeat
- [ ] **F5**: per-vertex `aAlpha` attribute encodes child member count weight — heavier edges render brighter (floor 0.3, max 1.0)
- [ ] **F6**: similarity edges use `LineDashedMaterial`; injection edges use `LineBasicMaterial`; both visibility-toggled via `clustersStore.showSimilarityEdges` / `showInjectionEdges`
- [ ] **F7**: BeamPool of 10 reusable `PlasmaBeam` instances; FPS-weapon NDC origin
- [ ] **F8**: `_breathingAnim` per-frame callback updates every cluster mesh + ring
- [ ] **F8**: per-node phase offset via `_nodePhaseOffsets` — deterministic hash of node ID → `[0, 2π]`
- [ ] **F8**: hover amplifies breathing to `±12%`
- [ ] **F8**: hover proximity field — clusters within 8 units of hovered cluster receive distance-attenuated breathing amplification (quadratic falloff, max 60% of full hover amplitude)
- [ ] **F9**: ClusterPhysics integration uses `k=120`, `d=12`, `dt clamp=0.1`, `velocityFloor=1e-4`
- [ ] **F9**: snap-to-target floor (prevents infinite micro-oscillation)
- [ ] **F10**: Neural Dust = 3000-particle `Points` cloud, `0x88ccff`, additive blending, slow XY rotation
- [ ] **F11**: 3 lights (Ambient 0.3, Directional 0.7 at (5,10,5), Hemisphere 0.2)
- [ ] **F12**: `shadowMap.enabled = true`, `type = PCFShadowMap`, `mapSize 1024×1024`
- [ ] **F13**: `EffectComposer` with `RenderPass` + `UnrealBloomPass(1.5, 0.4, 0.85)` + `FilmPass(0.35, false)` + `SMAAPass`
- [ ] **F13**: `composer.render()` not `renderer.render()`; `composer.setSize` on resize

### Interaction canon
- [ ] **F14**: `focusOn` with adaptive distance default — keeps current zoom when `distance` omitted, clamp `[5, 25]`
- [ ] **F14**: zero-length direction fallback to `+Z`
- [ ] **F14**: `_checkLod()` called per-frame inside the focus animate loop
- [ ] **F15**: `handleNodeClick` only calls `clustersStore.selectCluster(nodeId)` — no duplicate visual logic
- [ ] **F16**: `selectionController.afterRebuild()` at end of `rebuildScene` (replaces the pre-Sub-project-E inline `applyHighlight(focusedNodeId)` hack)
- [ ] **F16**: `SelectionController._applyHighlight` flips both `mat.color.setHex(HIGHLIGHT_COLOR)` AND `mat.emissive.setHex(HIGHLIGHT_COLOR)` (dual swap); `_clearHighlight` restores both from the `_highlightedColor` snapshot
- [ ] **F17**: `_hasAutoFocused` guard — bird's-eye-view zoom runs exactly once per lifecycle
- [ ] **F18**: external selection routes through `impactCoordinator.fire({ trigger: 'click', node, group })` — the coordinator's `fire()` body wraps `clusterPhysics.onBeamImpact` (and the F19 envelope + flash) inside the `beamPool.acquire(...)` config's `onImpact` callback. Selection-engulfment tracked in `SelectionController`'s internal engulfed set (Sub-project E migrated ownership from `ImpactCoordinator`); canonical read is `SelectionController.isEngulfed(id)`. Synchronous F19 reactions at the call site are forbidden — pinned by source-grep test #6.
- [ ] **F19**: `EnvelopePool` constructed in `onMount`; envelope geometry shape literals match `node.state === 'domain' ? 'domain' : 'cluster'`
- [ ] **F19**: `FlashController.flash()` uses baseline-capture pattern — rapid re-fires reuse the prior `baselineEmissive` AND `startIntensity` (B6 mid-flash continuity preserved through the FlashController class field `_states`)
- [ ] **F19**: causal-ordering invariant enforced at source level — every `impactCoordinator.fire({...})` site (click selection, entrance materialization burst, post-growth burst, optimization event) routes through the coordinator's `fire()` body, which wires `clusterPhysics.onBeamImpact` + `envelopePool.acquire` + `selectionController.onImpact(...)` (the impact-side flash delegation, post-Sub-project E) inside an `onImpact: () =>` callback, never synchronously alongside `beamPool.acquire()`. Per Sub-project C source-grep test #6: ZERO matches for those reaction patterns outside `ImpactCoordinator.ts`.
- [ ] All four impact trigger sites (entrance burst, post-growth burst, optimization event, click selection) route through ImpactCoordinator.fire(...) — pinned by source-grep test #5 in cleanup-contract.test.ts.
- [ ] Scene construction routes through 5 `SceneBuilder` instances (`ClusterBuilder`, `DomainBuilder`, `EdgeBuilder`, `RingBuilder`, `DustBuilder`) in cluster → domain → edge → ring → dust order per Scene Builder Ownership above. `rebuildScene` body is < 150 LOC; 5 `builder.build()` calls + 5 `builder.dispose()` calls pinned by `cleanup-contract.test.ts` §5.2 #4 + #13.

### Performance + lifecycle
- [ ] Module-level scratch table declared (`_scratchVec3a`, `_scratchQuat`, `_scratchColor`, `Z_AXIS`)
- [ ] `_breathingAnim` borrows `_scratchVec3a` for `mesh.scale.lerp` (not `new THREE.Vector3()`)
- [ ] Coordinators + 2 conditional cancellers wired in cleanup return in order: `selectionController?.dispose()` (Sub-project E — disposes inner `FlashController` and restores per-active-flash baseline emissive), `impactCoordinator?.dispose()` (Sub-project C), then `coordinator?.dispose()` (Sub-project B AnimationCoordinator), then `_removeFormationAnim?.()` + `_removeReadinessBillboard?.()` (Sub-project A conditional)
- [ ] `envelopePool?.dispose()` invoked + reference nulled in cleanup return
- [ ] `FlashController._states.clear()` happens inside `selectionController.dispose() → FlashController.dispose()` BEFORE `coordinator?.dispose()` tears down the AnimationCoordinator (so a half-cleared map can't be read by a late-firing tick); each active flash's `baselineEmissive` is restored to the underlying material before the map is cleared, with emissive color decided by live `isHighlighted` query (HIGHLIGHT_COLOR if still selected, captured `domainEmissive` otherwise)
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
