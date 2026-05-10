/**
 * Three.js scene manager for the taxonomy topology visualization.
 *
 * Owns: Scene, PerspectiveCamera, WebGLRenderer, OrbitControls, render loop.
 * Does NOT own: data transforms, interactions, labels (separate modules).
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

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
    this.scene.add(ambient);

    const directional = new THREE.DirectionalLight(0xffffff, 0.7);
    directional.position.set(5, 10, 5);
    directional.castShadow = true;
    directional.shadow.mapSize.set(1024, 1024);
    this.scene.add(directional);

    const hemisphere = new THREE.HemisphereLight(0x1a1a2e, 0x06060c, 0.2);
    this.scene.add(hemisphere);

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
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  /** Handle container resize. */
  resize(width: number, height: number): void {
    if (this._disposed) return;
    this.camera.aspect = width / height || 1;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  /** Animate camera to look at a target position.
   *
   *  Endpoint math is delegated to `computeFocusEndpoint` (pure function,
   *  unit-tested in `focus-math.test.ts`) for adaptive-distance clamping
   *  to `[controls.minDistance, controls.maxDistance]` and zero-length
   *  direction guard (camera-on-target case). The animation glue (RAF +
   *  `lerpVectors`) lives here. */
  focusOn(target: THREE.Vector3, distance = 20, duration = 600): void {
    // Cancel any in-flight focus animation
    if (this._focusAnimId != null) {
      cancelAnimationFrame(this._focusAnimId);
      this._focusAnimId = null;
    }
    if (this._disposed) return;

    const startPos = this.camera.position.clone();
    const startTarget = this.controls.target.clone();
    const { endPos, endTarget } = computeFocusEndpoint(
      startPos,
      startTarget,
      target,
      distance,
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

      if (t < 1) {
        this._focusAnimId = requestAnimationFrame(animate);
      } else {
        this._focusAnimId = null;
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
    this.renderer.dispose();
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
