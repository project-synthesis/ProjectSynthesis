// frontend/src/lib/components/taxonomy/BeamPool.ts
import * as THREE from 'three';
import { PlasmaBeam } from './PlasmaBeam';
import type { BeamConfig } from './PlasmaBeam';

const POOL_SIZE = 10;

export class BeamPool {
  readonly group: THREE.Group;
  private _beams: PlasmaBeam[] = [];
  private _origin = new THREE.Vector3();
  // Frame of reference center-bottom like an FPS weapon viewport origin, precisely on the near plane
  private _ndcOrigin = new THREE.Vector3(0.0, -1.0, -0.99);

  constructor() {
    this.group = new THREE.Group();
    this.group.userData.persistent = true;
    this.group.name = 'beam-pool';

    for (let i = 0; i < POOL_SIZE; i++) {
      const beam = new PlasmaBeam();
      this._beams.push(beam);
      this.group.add(beam.mesh);
    }
  }

  acquire(target: THREE.Object3D, config: BeamConfig, camera: THREE.PerspectiveCamera): PlasmaBeam | null {
    const beam = this._beams.find(b => b.state === 'idle');
    if (!beam) return null;
    this._origin.copy(this._ndcOrigin).unproject(camera);
    beam.fire(target, config, this._origin, camera);
    return beam;
  }

  update(delta: number, camera: THREE.PerspectiveCamera): void {
    this._origin.copy(this._ndcOrigin).unproject(camera);
    for (const beam of this._beams) {
      if (beam.state !== 'idle') {
        beam.update(delta, this._origin, camera);
      }
    }
  }

  terminateAll(): void {
    for (const beam of this._beams) {
      if (beam.state !== 'idle') {
        beam.terminate();
      }
    }
  }

  dispose(): void {
    for (const beam of this._beams) {
      beam.dispose();
    }
    this._beams = [];
  }
}
