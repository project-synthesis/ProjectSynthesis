/**
 * Billboard text labels for taxonomy nodes.
 *
 * Uses canvas-rendered textures on Three.js Sprites.
 * Only visible at near LOD tier. LRU-cached (max 500 sprites).
 */
import * as THREE from 'three';

const CANVAS_WIDTH = 256;
const CANVAS_HEIGHT = 64;
const FONT = '24px monospace';
const MAX_CACHE = 500;

export class TopologyLabels {
  private _group = new THREE.Group();
  private _cache = new Map<string, THREE.Sprite>();
  private _lruOrder: string[] = [];

  get group(): THREE.Group {
    return this._group;
  }

  /** Create or retrieve a cached sprite for the given label. */
  getOrCreate(id: string, text: string, color: string): THREE.Sprite {
    const key = `${id}:${text}:${color}`;
    const existing = this._cache.get(key);
    if (existing) {
      this._touchLru(key);
      return existing;
    }

    // Evict if at capacity
    if (this._cache.size >= MAX_CACHE) {
      this._evictOldest();
    }

    const texture = this._renderTexture(text, color);
    const material = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false,
    });
    const sprite = new THREE.Sprite(material);
    sprite.scale.set(CANVAS_WIDTH / 64, CANVAS_HEIGHT / 64, 1);

    this._cache.set(key, sprite);
    this._lruOrder.push(key);
    this._group.add(sprite);

    return sprite;
  }

  /** Remove all labels from the scene. */
  clear(): void {
    for (const sprite of this._cache.values()) {
      sprite.material.map?.dispose();
      sprite.material.dispose();
      this._group.remove(sprite);
    }
    this._cache.clear();
    this._lruOrder = [];
  }

  /** Set visibility of the entire label group. */
  setVisible(visible: boolean): void {
    this._group.visible = visible;
  }

  dispose(): void {
    this.clear();
  }

  private _renderTexture(text: string, color: string): THREE.CanvasTexture {
    const canvas = document.createElement('canvas');
    canvas.width = CANVAS_WIDTH;
    canvas.height = CANVAS_HEIGHT;
    const ctx = canvas.getContext('2d')!;

    ctx.clearRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
    ctx.font = FONT;
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // Truncate if too long
    let displayText = text;
    const maxWidth = CANVAS_WIDTH - 16;
    while (ctx.measureText(displayText).width > maxWidth && displayText.length > 3) {
      displayText = displayText.slice(0, -4) + '...';
    }

    ctx.fillText(displayText, CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2);

    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    return texture;
  }

  private _touchLru(key: string): void {
    const idx = this._lruOrder.indexOf(key);
    if (idx >= 0) {
      this._lruOrder.splice(idx, 1);
      this._lruOrder.push(key);
    }
  }

  private _evictOldest(): void {
    const key = this._lruOrder.shift();
    if (key) {
      const sprite = this._cache.get(key);
      if (sprite) {
        sprite.material.map?.dispose();
        sprite.material.dispose();
        this._group.remove(sprite);
        this._cache.delete(key);
      }
    }
  }
}
