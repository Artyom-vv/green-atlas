import type { Sphere } from 'three';
import { boundsSphere, readBounds } from './bounds2d';
import { instanceBounds } from './instanceBounds';
import {
  attribute,
  type Bounds2d,
  type CadAttribute,
  type CadCullingSummary,
  type CadGeometry,
} from './contracts';

/** Shared block vertices are read once; each geometry owns its instance union. */
export class CadBoundsCache {
  private positions = new WeakMap<CadAttribute, Bounds2d | undefined>();
  private geometries = new WeakMap<CadGeometry, Sphere | undefined>();

  constructor(private summary: CadCullingSummary) {}

  get(geometry: CadGeometry): Sphere | undefined {
    if (this.geometries.has(geometry)) return this.geometries.get(geometry);
    const position = attribute(geometry.attributes.position, 2);
    if (position && !this.positions.has(position))
      this.positions.set(position, readBounds(position, this.summary));
    const base = position && this.positions.get(position);
    const bounds = base && instanceBounds(base, geometry, this.summary);
    const sphere = bounds && boundsSphere(bounds);
    this.geometries.set(geometry, sphere);
    if (sphere) {
      geometry.boundingSphere = sphere;
      this.summary.boundedGeometries += 1;
    }
    return sphere;
  }
}
