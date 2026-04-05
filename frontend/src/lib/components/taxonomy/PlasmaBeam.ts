// frontend/src/lib/components/taxonomy/PlasmaBeam.ts
import * as THREE from 'three';
import {
  BEAM_VERTEX_SHADER,
  BEAM_FRAGMENT_SHADER,
  createBeamUniforms,
} from './BeamShader';

export type BeamState = 'idle' | 'firing' | 'sustain' | 'terminate';

export interface BeamConfig {
  colorEnd: THREE.Color;
  radius: number;
  sustainMs: number;
}

const RADIAL_SEGMENTS = 4;
const TUBULAR_SEGMENTS = 12;
const FIRING_MS = 300;
const TERMINATE_MS = 400;
const REBUILD_ANGLE_THRESHOLD = 0.035;
const REBUILD_DIST_THRESHOLD = 0.5;

export class PlasmaBeam {
  readonly mesh: THREE.Mesh;
  private _material: THREE.ShaderMaterial;
  private _geometry: THREE.BufferGeometry;
  private _state: BeamState = 'idle';
  private _stateTime = 0;
  private _config: BeamConfig | null = null;
  private _targetObject: THREE.Object3D | null = null;

  private _origin = new THREE.Vector3();
  private _target = new THREE.Vector3();
  private _control = new THREE.Vector3();
  private _lastCameraQuat = new THREE.Quaternion();
  private _lastTargetPos = new THREE.Vector3();
  private _curve = new THREE.QuadraticBezierCurve3(
    new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()
  );
  private _frameCounter = 0;
  private _sustainMs = 0;

  private _curvePoints: THREE.Vector3[] = [];
  private _tempVec = new THREE.Vector3();
  private _tempNormal = new THREE.Vector3();
  private _tempBinormal = new THREE.Vector3();
  private _tempUp = new THREE.Vector3();

  // Pre-computed sin/cos table for radial vertex placement (avoids trig per frame)
  private _cosTable: Float32Array;
  private _sinTable: Float32Array;

  constructor() {
    // Pre-compute sin/cos for radial segments (values never change)
    this._cosTable = new Float32Array(RADIAL_SEGMENTS + 1);
    this._sinTable = new Float32Array(RADIAL_SEGMENTS + 1);
    for (let j = 0; j <= RADIAL_SEGMENTS; j++) {
      const angle = (j / RADIAL_SEGMENTS) * Math.PI * 2;
      this._cosTable[j] = Math.cos(angle);
      this._sinTable[j] = Math.sin(angle);
    }

    for (let i = 0; i <= TUBULAR_SEGMENTS; i++) {
      this._curvePoints.push(new THREE.Vector3());
    }

    const vertexCount = (TUBULAR_SEGMENTS + 1) * (RADIAL_SEGMENTS + 1);
    const indexCount = TUBULAR_SEGMENTS * RADIAL_SEGMENTS * 6;

    this._geometry = new THREE.BufferGeometry();
    this._geometry.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array(vertexCount * 3), 3)
    );
    this._geometry.setAttribute(
      'uv',
      new THREE.BufferAttribute(new Float32Array(vertexCount * 2), 2)
    );
    this._geometry.setIndex(
      new THREE.BufferAttribute(new Uint16Array(indexCount), 1)
    );

    this._buildIndices();
    this._buildUVs();

    this._material = new THREE.ShaderMaterial({
      uniforms: createBeamUniforms(),
      vertexShader: BEAM_VERTEX_SHADER,
      fragmentShader: BEAM_FRAGMENT_SHADER,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
      wireframe: false,
    });

    this.mesh = new THREE.Mesh(this._geometry, this._material);
    this.mesh.visible = false;
    this.mesh.frustumCulled = false;
  }

  get state(): BeamState {
    return this._state;
  }

  fire(
    target: THREE.Object3D,
    config: BeamConfig,
    origin: THREE.Vector3,
    camera: THREE.PerspectiveCamera
  ): void {
    this._targetObject = target;
    this._config = config;
    this._state = 'firing';
    this._stateTime = 0;
    this._frameCounter = 0;

    this._material.uniforms.uColorEnd.value.copy(config.colorEnd);
    this._material.uniforms.uOpacity.value = 0;
    this._material.uniforms.uFlowSpeed.value = 2.0;
    this._material.uniforms.uThickness.value = 1.0;
    this._sustainMs = config.sustainMs;

    this._updateCurve(origin, camera, true);
    this.mesh.visible = true;

    this._lastCameraQuat.copy(camera.quaternion);
    target.getWorldPosition(this._lastTargetPos);
  }

  terminate(): void {
    if (this._state === 'idle' || this._state === 'terminate') return;
    this._state = 'terminate';
    this._stateTime = 0;
  }

  update(
    delta: number,
    origin: THREE.Vector3,
    camera: THREE.PerspectiveCamera
  ): boolean {
    if (this._state === 'idle') return false;
    if (!this._config) return false;

    this._stateTime += delta * 1000;
    this._frameCounter++;

    this._material.uniforms.uTime.value += delta;

    switch (this._state) {
      case 'firing': {
        const t = Math.min(this._stateTime / FIRING_MS, 1);
        this._material.uniforms.uOpacity.value = t;
        this._updateCurve(origin, camera, true);
        if (t >= 1) {
          this._state = 'sustain';
          this._stateTime = 0;
        }
        break;
      }
      case 'sustain': {
        this._material.uniforms.uOpacity.value = 1.0;
        const needsRebuild = this._frameCounter % 3 === 0 && this._needsCurveRebuild(camera);
        if (needsRebuild) {
          this._updateCurve(origin, camera, false);
        }
        if (this._sustainMs !== Infinity && this._stateTime >= this._sustainMs) {
          this._state = 'terminate';
          this._stateTime = 0;
        }
        break;
      }
      case 'terminate': {
        const t = Math.min(this._stateTime / TERMINATE_MS, 1);
        this._material.uniforms.uOpacity.value = 1.0 - t;
        this._updateCurve(origin, camera, true);
        if (t >= 1) {
          this._reset();
          return false;
        }
        break;
      }
    }

    return true;
  }

  private _reset(): void {
    this._state = 'idle';
    this._stateTime = 0;
    this._config = null;
    this._targetObject = null;
    this.mesh.visible = false;
    this._material.uniforms.uOpacity.value = 0;
    this._material.uniforms.uTime.value = 0;
    this._material.uniforms.uThickness.value = 1.0;
  }

  private _needsCurveRebuild(camera: THREE.PerspectiveCamera): boolean {
    const angleDiff = camera.quaternion.angleTo(this._lastCameraQuat);
    if (angleDiff > REBUILD_ANGLE_THRESHOLD) return true;
    if (!this._targetObject) return false;
    this._targetObject.getWorldPosition(this._tempVec);
    return this._tempVec.distanceTo(this._lastTargetPos) > REBUILD_DIST_THRESHOLD;
  }

  private _updateCurve(
    origin: THREE.Vector3,
    camera: THREE.PerspectiveCamera,
    force: boolean
  ): void {
    if (!this._targetObject) return;
    if (!force && !this._needsCurveRebuild(camera)) return;

    this._origin.copy(origin);
    this._targetObject.getWorldPosition(this._target);

    this._control.addVectors(this._origin, this._target).multiplyScalar(0.5);
    const dist = this._origin.distanceTo(this._target);
    const sag = dist * 0.15;
    this._tempVec.copy(camera.up).negate().multiplyScalar(sag);
    this._control.add(this._tempVec);

    this._curve.v0.copy(this._origin);
    this._curve.v1.copy(this._control);
    this._curve.v2.copy(this._target);

    for (let i = 0; i <= TUBULAR_SEGMENTS; i++) {
      this._curve.getPoint(i / TUBULAR_SEGMENTS, this._curvePoints[i]);
    }

    this._buildPositions();

    this._lastCameraQuat.copy(camera.quaternion);
    this._lastTargetPos.copy(this._target);
  }

  private _buildPositions(): void {
    const posAttr = this._geometry.getAttribute('position') as THREE.BufferAttribute;
    const posArray = posAttr.array as Float32Array;
    const radius = this._config?.radius ?? 0.04;

    let idx = 0;
    for (let i = 0; i <= TUBULAR_SEGMENTS; i++) {
      const P = this._curvePoints[i];

      let tangent: THREE.Vector3;
      if (i < TUBULAR_SEGMENTS) {
        tangent = this._tempVec.subVectors(this._curvePoints[i + 1], P).normalize();
      } else {
        tangent = this._tempVec.subVectors(P, this._curvePoints[i - 1]).normalize();
      }

      if (Math.abs(tangent.y) > 0.99) {
        this._tempUp.set(1, 0, 0);
      } else {
        this._tempUp.set(0, 1, 0);
      }
      const normal = this._tempNormal.crossVectors(tangent, this._tempUp).normalize();
      const binormal = this._tempBinormal.crossVectors(tangent, normal).normalize();

      for (let j = 0; j <= RADIAL_SEGMENTS; j++) {
        const cos = this._cosTable[j];
        const sin = this._sinTable[j];

        posArray[idx++] = P.x + radius * (cos * normal.x + sin * binormal.x);
        posArray[idx++] = P.y + radius * (cos * normal.y + sin * binormal.y);
        posArray[idx++] = P.z + radius * (cos * normal.z + sin * binormal.z);
      }
    }

    posAttr.needsUpdate = true;
    this._geometry.computeBoundingSphere();
  }

  private _buildIndices(): void {
    const indexAttr = this._geometry.getIndex()!;
    const indices = indexAttr.array as Uint16Array;
    let idx = 0;

    for (let i = 0; i < TUBULAR_SEGMENTS; i++) {
      for (let j = 0; j < RADIAL_SEGMENTS; j++) {
        const a = i * (RADIAL_SEGMENTS + 1) + j;
        const b = a + RADIAL_SEGMENTS + 1;
        const c = a + 1;
        const d = b + 1;

        indices[idx++] = a;
        indices[idx++] = b;
        indices[idx++] = c;
        indices[idx++] = c;
        indices[idx++] = b;
        indices[idx++] = d;
      }
    }

    indexAttr.needsUpdate = true;
  }

  private _buildUVs(): void {
    const uvAttr = this._geometry.getAttribute('uv') as THREE.BufferAttribute;
    const uvArray = uvAttr.array as Float32Array;
    let idx = 0;

    for (let i = 0; i <= TUBULAR_SEGMENTS; i++) {
      for (let j = 0; j <= RADIAL_SEGMENTS; j++) {
        uvArray[idx++] = i / TUBULAR_SEGMENTS;
        uvArray[idx++] = j / RADIAL_SEGMENTS;
      }
    }

    uvAttr.needsUpdate = true;
  }

  dispose(): void {
    this._geometry.dispose();
    this._material.dispose();
  }
}
