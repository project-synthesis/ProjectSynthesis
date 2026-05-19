/**
 * Three.js scene manager for the taxonomy topology visualization.
 *
 * Owns: Scene, PerspectiveCamera, WebGLRenderer, OrbitControls, render loop.
 * Does NOT own: data transforms, interactions, labels (separate modules).
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { FilmPass } from 'three/addons/postprocessing/FilmPass.js';
import { SMAAPass } from 'three/addons/postprocessing/SMAAPass.js';

import { computeFocusEndpoint } from './focus-math';

export type LODTier = 'far' | 'mid' | 'near';

export interface RendererOptions {
  antialias?: boolean;
  background?: string;
}

const DEFAULT_BG = 0x06060c;
const FAR_DISTANCE = 50;
const MID_DISTANCE = 15;

export class TopologyRenderer {
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly renderer: THREE.WebGLRenderer;
  readonly controls: OrbitControls;
  readonly composer: EffectComposer;

  private _animationId: number | null = null;
  private _focusAnimId: number | null = null;
  private _disposed = false;
  private _onLodChange: ((tier: LODTier) => void) | null = null;
  private _animateCallbacks: (() => void)[] = [];
  private _currentLod: LODTier = 'far';

  constructor(canvas: HTMLCanvasElement, opts?: RendererOptions) {
    // Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(opts?.background ?? DEFAULT_BG);

    // Camera
    const aspect = canvas.clientWidth / canvas.clientHeight || 1;
    this.camera = new THREE.PerspectiveCamera(60, aspect, 0.1, 500);
    this.camera.position.set(0, 0, 80);

    // Renderer
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: opts?.antialias ?? true,
      alpha: false,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);

    // Shadow map: PCFShadowMap is the canonical replacement for the
    // deprecated PCFSoftShadowMap (removed in three@0.170+). Brand reference:
    // .claude/skills/brand-guidelines/references/3d-visualization.md "ShadowMap defaults".
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;

    // Lighting — three lights per brand reference "Lighting Setups":
    //   AmbientLight     — baseline scene fill, intensity 0.3, white.
    //   DirectionalLight — primary key light at +5 +10 +5, intensity 0.7,
    //                      white, castShadow with 1024² map.
    //   HemisphereLight  — sky #1a1a2e (matches DEFAULT_BG) / ground
    //                      #06060c, intensity 0.2 — gives the dark void
    //                      a top-down organic feel.
    const ambient = new THREE.AmbientLight(0xffffff, 0.3);
    ambient.userData.persistent = true;
    this.scene.add(ambient);

    const directional = new THREE.DirectionalLight(0xffffff, 0.7);
    directional.userData.persistent = true;
    directional.position.set(5, 10, 5);
    directional.castShadow = true;
    directional.shadow.mapSize.set(1024, 1024);
    this.scene.add(directional);

    const hemisphere = new THREE.HemisphereLight(0x1a1a2e, 0x06060c, 0.2);
    hemisphere.userData.persistent = true;
    this.scene.add(hemisphere);

    // Cinematic post-processing pipeline (canon F13). The 3D Pattern
    // Graph is its own medium — UnrealBloomPass amplifies emissive
    // surfaces (the "alive" feeling), FilmPass adds subtle cinematic
    // grain (texture without noise). Render via composer.render() in
    // the start() loop instead of renderer.render().
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.composer.addPass(
      new UnrealBloomPass(
        new THREE.Vector2(canvas.clientWidth, canvas.clientHeight),
        1.5, // strength
        0.4, // radius
        0.85, // threshold
      ),
    );
    this.composer.addPass(new FilmPass(0.35, false));
    this.composer.addPass(new SMAAPass());

    // Controls
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 200;
    this.controls.addEventListener('change', () => this._checkLod());
  }

  /** Current LOD tier based on camera distance. */
  get lodTier(): LODTier {
    return this._currentLod;
  }

  /** Register a callback for LOD tier changes. */
  onLodChange(cb: (tier: LODTier) => void): void {
    this._onLodChange = cb;
  }

  /** Register a per-frame callback (called before render). Returns unsubscribe function. */
  addAnimationCallback(cb: () => void): () => void {
    this._animateCallbacks.push(cb);
    return () => {
      const idx = this._animateCallbacks.indexOf(cb);
      if (idx >= 0) this._animateCallbacks.splice(idx, 1);
    };
  }

  /** Start the render loop. */
  start(): void {
    if (this._disposed) return;
    const loop = () => {
      if (this._disposed) return;
      this._animationId = requestAnimationFrame(loop);
      this.controls.update();
      for (const cb of this._animateCallbacks) cb();
      this.composer.render(); // canon F13 — bloom + film grain pipeline
    };
    loop();
  }

  /** Handle container resize. */
  resize(width: number, height: number): void {
    if (this._disposed) return;
    this.camera.aspect = width / height || 1;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height); // canon F13 — keep composer in sync
  }

  /** Animate camera to look at a target position (canon F14).
   *
   *  Adaptive distance: when `distance` is omitted, the existing camera
   *  zoom is preserved (clamped to `[5, 25]`) so a new selection doesn't
   *  yank the user out of their current zoom level. Explicit `distance`
   *  overrides — e.g. `focusOn(origin, 80)` for the deselect-to-overview.
   *
   *  Endpoint math is delegated to `computeFocusEndpoint` (pure function,
   *  unit-tested in `focus-math.test.ts`) for the zero-length direction
   *  guard. The animation glue (RAF + `lerpVectors`) lives here.
   *
   *  `_checkLod()` is fired per-frame inside the animate loop because
   *  `OrbitControls.update()` does NOT fire 'change' events when camera
   *  position is mutated programmatically without damping residual.
   *  Without this call, LOD tier changes silently lag the camera.
   *
   *  Sub-project E (B8): Append-only 4th positional `onComplete?: () => void`
   *  fires after tween completes (NOT on cancellation — a subsequent focusOn
   *  call cancels the prior animate loop via `cancelAnimationFrame`, and the
   *  cancelled closure never reaches the done branch). Does NOT fire on dispose
   *  (the `if (this._disposed) return;` guard at top of `animate` short-circuits).
   *
   *  Existing callers (TopologyInteraction.ts:72, SemanticTopology.svelte:1213 + :1492)
   *  pass fewer than 4 args — those continue to work unchanged. */
  focusOn(
    target: THREE.Vector3,
    distance?: number,
    duration = 600,
    onComplete?: () => void,
  ): void {
    // Cancel any in-flight focus animation
    if (this._focusAnimId != null) {
      cancelAnimationFrame(this._focusAnimId);
      this._focusAnimId = null;
    }
    if (this._disposed) return;

    const startPos = this.camera.position.clone();
    const startTarget = this.controls.target.clone();

    // Adaptive default: preserve current zoom if `distance` omitted.
    const currentDist = startPos.distanceTo(startTarget);
    const targetDist = distance !== undefined
      ? distance
      : Math.max(5, Math.min(currentDist, 25));

    const { endPos, endTarget } = computeFocusEndpoint(
      startPos,
      startTarget,
      target,
      targetDist,
      {
        minDistance: this.controls.minDistance,
        maxDistance: this.controls.maxDistance,
      },
    );

    const startTime = performance.now();
    const animate = () => {
      if (this._disposed) return;
      const elapsed = performance.now() - startTime;
      const t = Math.min(elapsed / duration, 1);
      const ease = 1 - Math.pow(1 - t, 3); // ease-out cubic

      this.camera.position.lerpVectors(startPos, endPos, ease);
      this.controls.target.lerpVectors(startTarget, endTarget, ease);
      this.controls.update();

      // Canon F14: programmatic position mutation does not fire OrbitControls
      // 'change' events when there's no damping residual — _checkLod must be
      // called explicitly to keep LOD tier in sync with camera distance.
      this._checkLod();

      if (t < 1) {
        this._focusAnimId = requestAnimationFrame(animate);
      } else {
        this._focusAnimId = null;
        if (onComplete) onComplete();
      }
    };
    animate();
  }

  /** Clean up all Three.js resources. */
  dispose(): void {
    this._disposed = true;
    if (this._animationId != null) {
      cancelAnimationFrame(this._animationId);
    }
    if (this._focusAnimId != null) {
      cancelAnimationFrame(this._focusAnimId);
    }
    this.controls.dispose();
    // Composer disposal (canon F13) — disposes each pass's render target +
    // any internal materials. Must run before renderer.dispose().
    for (const pass of this.composer.passes) {
      if (typeof (pass as { dispose?: () => void }).dispose === 'function') {
        (pass as { dispose: () => void }).dispose();
      }
    }
    this.composer.dispose();
    this.renderer.dispose();
    // Force GL context release per canon "Disposal Contract" — prevents
    // GL context accumulation on rapid mount/unmount cycles (e.g. HMR
    // during dev, or SvelteKit navigation in/out of /app).
    this.renderer.forceContextLoss();
    this._animateCallbacks.length = 0;
    this.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh || obj instanceof THREE.LineSegments || obj instanceof THREE.Points) {
        obj.geometry.dispose();
        if (Array.isArray(obj.material)) {
          obj.material.forEach((m: THREE.Material) => m.dispose());
        } else {
          (obj.material as THREE.Material).dispose();
        }
      } else if (obj instanceof THREE.Sprite) {
        obj.material.map?.dispose();
        obj.material.dispose();
      } else if (
        obj instanceof THREE.DirectionalLight ||
        obj instanceof THREE.SpotLight ||
        obj instanceof THREE.PointLight
      ) {
        // Shadow-casting lights lazily allocate a `WebGLRenderTarget` for
        // the shadow map on first render. `renderer.dispose()` releases
        // context-level resources but does not release per-light shadow
        // maps — explicit dispose avoids a small GPU resource leak on
        // remount. AmbientLight + HemisphereLight have no shadow map.
        // (Brand reference: "Disposal Contract" — every GPU resource has
        // an explicit owner.)
        obj.shadow?.map?.dispose();
      }
    });
  }

  private _checkLod(): void {
    const dist = this.camera.position.distanceTo(this.controls.target);
    let tier: LODTier;
    if (dist > FAR_DISTANCE) tier = 'far';
    else if (dist > MID_DISTANCE) tier = 'mid';
    else tier = 'near';

    if (tier !== this._currentLod) {
      this._currentLod = tier;
      this._onLodChange?.(tier);
    }
  }
}
