import type { Sphere } from 'three';
import type { CadObject } from './contracts';
import { worldScale } from './worldScale';

interface LineOwner {
  object: CadObject;
  pixels: number;
}

/** Pixel-wide lines can reach the viewport while their centerline is outside. */
export class LineMargins {
  private bounds = new Map<
    Sphere,
    { baseRadius: number; owners: LineOwner[] }
  >();

  add(sphere: Sphere, object: CadObject, pixels: number): void {
    let bound = this.bounds.get(sphere);
    if (!bound) {
      bound = { baseRadius: sphere.radius, owners: [] };
      this.bounds.set(sphere, bound);
    }
    bound.owners.push({ object, pixels });
  }

  update(sourceResolution: number): void {
    for (const [sphere, bound] of this.bounds) {
      let padding = 0;
      for (const { object, pixels } of bound.owners) {
        const scale = worldScale(object.matrixWorld) ?? NaN;
        const margin = (pixels * sourceResolution) / scale;
        const valid =
          scale > 0 &&
          sourceResolution > 0 &&
          Number.isFinite(scale) &&
          Number.isFinite(margin);
        object.frustumCulled = valid;
        if (valid) padding = Math.max(padding, margin);
      }
      // Several objects may share geometry; use the largest local margin.
      sphere.radius = bound.baseRadius + padding;
    }
  }
}
