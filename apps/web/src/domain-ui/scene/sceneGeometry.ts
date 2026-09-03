import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

export type SceneContextLike = {
  feature_id: string;
  kind: string;
  geometry: { [key: string]: unknown };
  label?: string | null;
  height_m?: number | null;
  height_status?: 'confirmed' | 'estimated' | 'missing' | null;
  height_source?: string | null;
};

type LocalPoint = [number, number];

const CONTEXT_COLORS: Record<string, number> = {
  allowed: 0x89a98f,
  building: 0xc8c2b8,
  existing_green: 0x729477,
  restricted: 0xc78272,
  road: 0x9da3a5,
  site_border: 0x49636f,
  utility: 0xb59159,
  water: 0x6f9eaa,
};

// Most DXF polygons are semantic boundaries, not physical surfaces. Filling
// all of them stacks large transparent sheets over the same pixels, washes
// out the plan and is disproportionately expensive on dense city drawings.
const SURFACE_KINDS = new Set(['building', 'road', 'water']);

function ringPoints(value: unknown): LocalPoint[] {
  return Array.isArray(value)
    ? value
      .filter((point): point is number[] => Array.isArray(point) && typeof point[0] === 'number' && typeof point[1] === 'number')
      .map((point) => [point[0], point[1]])
    : [];
}

function polygonRings(value: unknown): LocalPoint[][] {
  return Array.isArray(value) ? value.map(ringPoints).filter((ring) => ring.length >= 3) : [];
}

function featureLines(feature: SceneContextLike): LocalPoint[][] {
  const geometry = feature.geometry as { type?: string; coordinates?: unknown };
  const coordinates = geometry.coordinates;
  if (geometry.type === 'LineString') return [ringPoints(coordinates)];
  if (geometry.type === 'MultiLineString') return (Array.isArray(coordinates) ? coordinates : []).map(ringPoints);
  if (geometry.type === 'Polygon') return polygonRings(coordinates);
  if (geometry.type === 'MultiPolygon') {
    return (Array.isArray(coordinates) ? coordinates : []).flatMap(polygonRings);
  }
  return [];
}

function featurePolygons(feature: SceneContextLike): LocalPoint[][][] {
  const geometry = feature.geometry as { type?: string; coordinates?: unknown };
  const coordinates = geometry.coordinates;
  if (geometry.type === 'Polygon') return [polygonRings(coordinates)];
  if (geometry.type === 'MultiPolygon') return (Array.isArray(coordinates) ? coordinates : []).map(polygonRings);
  return [];
}

function addLineSegments(target: number[], points: LocalPoint[], elevation = 0.035) {
  for (let index = 1; index < points.length; index += 1) {
    const [x1, y1] = points[index - 1];
    const [x2, y2] = points[index];
    target.push(x1, elevation, -y1, x2, elevation, -y2);
  }
}

function makeShape(rings: LocalPoint[][]) {
  const [outer, ...holes] = rings;
  if (!outer?.length) return undefined;
  const shape = new THREE.Shape(outer.map(([x, y]) => new THREE.Vector2(x, -y)));
  shape.holes = holes.map((ring) => new THREE.Path(ring.map(([x, y]) => new THREE.Vector2(x, -y))));
  return shape;
}

function confirmedHeight(feature: SceneContextLike) {
  if (feature.kind !== 'building' || feature.height_status !== 'confirmed') return undefined;
  if (typeof feature.height_m !== 'number' || !Number.isFinite(feature.height_m) || feature.height_m <= 0) return undefined;
  return Math.min(feature.height_m, 180);
}

export type ContextScene = {
  group: THREE.Group;
  bounds: THREE.Box3;
  confirmedBuildingCount: number;
  flatBuildingCount: number;
};

/**
 * Converts the DXF context to a deliberately restrained planning model.
 * Unknown building heights stay as flat footprints; this renderer never
 * fabricates a storey count or terrain elevation.
 */
export function buildContextScene(features: SceneContextLike[]): ContextScene {
  const group = new THREE.Group();
  group.name = 'dxf-context';
  const bounds = new THREE.Box3();
  const linePositions = new Map<string, number[]>();
  const fillGeometries = new Map<string, THREE.BufferGeometry[]>();
  let confirmedBuildingCount = 0;
  let flatBuildingCount = 0;

  for (const feature of features) {
    const kind = feature.kind in CONTEXT_COLORS ? feature.kind : 'site_border';
    const lines = linePositions.get(kind) ?? [];
    for (const points of featureLines(feature)) {
      addLineSegments(lines, points, kind === 'building' ? 0.055 : 0.035);
      for (const [x, y] of points) bounds.expandByPoint(new THREE.Vector3(x, 0, -y));
    }
    linePositions.set(kind, lines);

    for (const rings of featurePolygons(feature)) {
      if (!SURFACE_KINDS.has(kind)) continue;
      const shape = makeShape(rings);
      if (!shape) continue;
      const height = confirmedHeight(feature);
      let geometry: THREE.BufferGeometry;
      if (height) {
        geometry = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false, curveSegments: 1 });
        geometry.rotateX(-Math.PI / 2);
        confirmedBuildingCount += 1;
        bounds.max.y = Math.max(bounds.max.y, height);
      } else {
        geometry = new THREE.ShapeGeometry(shape, 1);
        geometry.rotateX(-Math.PI / 2);
        geometry.translate(0, kind === 'water' ? 0.018 : 0.012, 0);
        if (kind === 'building') flatBuildingCount += 1;
      }
      // ShapeGeometry and ExtrudeGeometry do not consistently agree on
      // indexed storage. A project may contain both flat (unknown height) and
      // extruded (confirmed height) buildings, so normalise before batching;
      // otherwise BufferGeometryUtils rejects the whole fill batch.
      if (geometry.index) {
        const indexed = geometry;
        geometry = geometry.toNonIndexed();
        indexed.dispose();
      }
      const geometries = fillGeometries.get(kind) ?? [];
      geometries.push(geometry);
      fillGeometries.set(kind, geometries);
    }
  }

  for (const [kind, positions] of linePositions) {
    if (!positions.length) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const material = new THREE.LineBasicMaterial({
      color: CONTEXT_COLORS[kind],
      transparent: true,
      opacity: kind === 'site_border' ? 0.92 : 0.66,
      depthWrite: false,
    });
    const lines = new THREE.LineSegments(geometry, material);
    lines.name = `context-lines:${kind}`;
    lines.renderOrder = kind === 'site_border' ? 4 : 2;
    group.add(lines);
  }

  for (const [kind, geometries] of fillGeometries) {
    const merged = mergeGeometries(geometries, false);
    geometries.forEach((geometry) => geometry.dispose());
    if (!merged) continue;
    merged.computeVertexNormals();
    const material = new THREE.MeshStandardMaterial({
      color: CONTEXT_COLORS[kind],
      roughness: kind === 'water' ? 0.34 : 0.9,
      metalness: 0,
      transparent: kind !== 'building',
      opacity: kind === 'water' ? 0.48 : kind === 'road' ? 0.5 : kind === 'building' ? 1 : 0.2,
      side: THREE.DoubleSide,
      depthWrite: kind === 'building' || kind === 'road',
    });
    const mesh = new THREE.Mesh(merged, material);
    mesh.name = `context-fill:${kind}`;
    mesh.receiveShadow = kind === 'road' || kind === 'building';
    mesh.castShadow = kind === 'building' && confirmedBuildingCount > 0;
    mesh.renderOrder = 1;
    group.add(mesh);
  }

  if (bounds.isEmpty()) bounds.setFromCenterAndSize(new THREE.Vector3(), new THREE.Vector3(40, 1, 40));
  return { group, bounds, confirmedBuildingCount, flatBuildingCount };
}

export function disposeObjectTree(root: THREE.Object3D) {
  const materials = new Set<THREE.Material>();
  const geometries = new Set<THREE.BufferGeometry>();
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.LineSegments)) return;
    geometries.add(object.geometry);
    const objectMaterials = Array.isArray(object.material) ? object.material : [object.material];
    objectMaterials.forEach((material) => materials.add(material));
  });
  geometries.forEach((geometry) => geometry.dispose());
  materials.forEach((material) => material.dispose());
}
