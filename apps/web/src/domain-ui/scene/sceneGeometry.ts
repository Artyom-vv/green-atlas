import * as THREE from 'three';
import { mergeGeometries, mergeVertices } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

export type SceneContextLike = {
  feature_id: string;
  kind: string;
  geometry: { [key: string]: unknown };
  label?: string | null;
  base_elevation_m?: number | null;
  base_elevation_source?: string | null;
  height_m?: number | null;
  height_status?: 'confirmed' | 'estimated' | 'missing' | null;
  height_source?: string | null;
};

export type SceneVerticalPrimitiveLike = {
  primitive_type: 'point' | 'polyline' | 'surface_mesh';
  vertices?: number[][];
  faces?: number[][];
  terrain_mapping_status?: 'unmapped' | 'confirmed';
};

type LocalPoint = [number, number];

const CONTEXT_COLORS: Record<string, number> = {
  allowed: 0x3f7d58,
  building: 0x8797a5,
  existing_green: 0x28744b,
  restricted: 0xbc4f42,
  road: 0xccd3d6,
  site_border: 0x173f4b,
  utility: 0xa36b25,
  water: 0x8ebdcf,
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

function addLineSegments(target: number[], points: LocalPoint[], elevation: number | ((x: number, y: number) => number) = 0.035) {
  for (let index = 1; index < points.length; index += 1) {
    const [x1, y1] = points[index - 1];
    const [x2, y2] = points[index];
    const steps = typeof elevation === 'function' ? Math.min(512, Math.max(1, Math.ceil(Math.hypot(x2 - x1, y2 - y1) / 5))) : 1;
    for (let step = 0; step < steps; step++) for (const t of [step / steps, (step + 1) / steps]) {
      const x = x1 + (x2 - x1) * t, y = y1 + (y2 - y1) * t;
      target.push(x, typeof elevation === 'function' ? elevation(x, y) : elevation, -y);
    }
  }
}

function makeShape(rings: LocalPoint[][]) {
  const [outer, ...holes] = rings;
  if (!outer?.length) return undefined;
  // The subsequent -90° X rotation already maps source +Y to world -Z,
  // matching linework and plants. Negating here as well mirrors every fill.
  const shape = new THREE.Shape(outer.map(([x, y]) => new THREE.Vector2(x, y)));
  shape.holes = holes.map((ring) => new THREE.Path(ring.map(([x, y]) => new THREE.Vector2(x, y))));
  return shape;
}

function modelHeight(feature: SceneContextLike) {
  // Estimated OSM storey heights are allowed in the visual model because the
  // scene passport exposes their status and source. Suppressing them made a
  // truthful-but-estimated city look flat and was less informative than the
  // explicitly labelled LoD1 volume.
  if (feature.kind !== 'building' || !['confirmed', 'estimated'].includes(feature.height_status ?? 'missing')) return undefined;
  if (typeof feature.height_m !== 'number' || !Number.isFinite(feature.height_m) || feature.height_m <= 0) return undefined;
  return Math.min(feature.height_m, 180);
}

export type ContextScene = {
  group: THREE.Group;
  bounds: THREE.Box3;
  confirmedBuildingCount: number;
  flatBuildingCount: number;
  terrainElevationAt: (x: number, z: number) => number;
  hasTerrain: boolean;
};

type TerrainTriangle = { a: THREE.Vector3; b: THREE.Vector3; c: THREE.Vector3; minX: number; maxX: number; minZ: number; maxZ: number };

function terrainTriangles(primitives: SceneVerticalPrimitiveLike[]) {
  const triangles: TerrainTriangle[] = [];
  const positions: number[] = [];
  for (const primitive of primitives) {
    if (primitive.primitive_type !== 'surface_mesh' || primitive.terrain_mapping_status !== 'confirmed') continue;
    const vertices = primitive.vertices ?? [];
    for (const face of primitive.faces ?? []) {
      for (let index = 1; index + 1 < face.length; index += 1) {
        const source = [face[0], face[index], face[index + 1]].map((vertexIndex) => vertices[vertexIndex]);
        if (source.some((vertex) => !vertex || vertex.length < 3)) continue;
        const [a, b, c] = source.map((vertex) => new THREE.Vector3(vertex[0], vertex[2], -vertex[1]));
        positions.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
        triangles.push({
          a, b, c,
          minX: Math.min(a.x, b.x, c.x), maxX: Math.max(a.x, b.x, c.x),
          minZ: Math.min(a.z, b.z, c.z), maxZ: Math.max(a.z, b.z, c.z),
        });
      }
    }
  }
  return { triangles, positions };
}

function terrainSampler(triangles: TerrainTriangle[]) {
  return (x: number, z: number) => {
    for (const triangle of triangles) {
      if (x < triangle.minX || x > triangle.maxX || z < triangle.minZ || z > triangle.maxZ) continue;
      const { a, b, c } = triangle;
      const denominator = (b.z - c.z) * (a.x - c.x) + (c.x - b.x) * (a.z - c.z);
      if (Math.abs(denominator) < 1e-8) continue;
      const wa = ((b.z - c.z) * (x - c.x) + (c.x - b.x) * (z - c.z)) / denominator;
      const wb = ((c.z - a.z) * (x - c.x) + (a.x - c.x) * (z - c.z)) / denominator;
      const wc = 1 - wa - wb;
      if (wa >= -1e-5 && wb >= -1e-5 && wc >= -1e-5) return wa * a.y + wb * b.y + wc * c.y;
    }
    return 0;
  };
}

/**
 * Converts the DXF context to a deliberately restrained planning model.
 * Unknown building heights stay as flat footprints; this renderer never
 * fabricates a storey count or terrain elevation.
 */
export function buildContextScene(features: SceneContextLike[], verticalPrimitives: SceneVerticalPrimitiveLike[] = []): ContextScene {
  const group = new THREE.Group();
  group.name = 'dxf-context';
  const bounds = new THREE.Box3();
  const linePositions = new Map<string, number[]>();
  const fillGeometries = new Map<string, THREE.BufferGeometry[]>();
  let confirmedBuildingCount = 0;
  let flatBuildingCount = 0;
  const terrain = terrainTriangles(verticalPrimitives);
  const terrainElevationAt = terrainSampler(terrain.triangles);
  const hasTerrain = terrain.positions.length > 0;

  for (const feature of features) {
    if (feature.kind === 'planting_area' || feature.kind === 'allowed') continue;
    const kind = feature.kind in CONTEXT_COLORS ? feature.kind : 'site_border';
    const featureHeight = modelHeight(feature);
    const baseElevation = typeof feature.base_elevation_m === 'number' ? feature.base_elevation_m : 0;
    const lineOffset = kind === 'building'
      ? (featureHeight ?? 0.012) + 0.008
      : kind === 'road'
        ? 0.065
        : kind === 'water'
          ? 0.01
          : 0.035;
    const lineElevation = hasTerrain && baseElevation === 0
      ? (x: number, y: number) => terrainElevationAt(x, -y) + lineOffset
      : baseElevation + lineOffset;
    const lines = linePositions.get(kind) ?? [];
    for (const points of featureLines(feature)) {
      addLineSegments(lines, points, lineElevation);
      for (const [x, y] of points) bounds.expandByPoint(new THREE.Vector3(x, 0, -y));
    }
    linePositions.set(kind, lines);

    for (const rings of featurePolygons(feature)) {
      if (!SURFACE_KINDS.has(kind)) continue;
      const shape = makeShape(rings);
      if (!shape) continue;
      const terrainBase = hasTerrain && baseElevation === 0 && rings[0]?.length
        ? terrainElevationAt(rings[0][0][0], -rings[0][0][1])
        : baseElevation;
      const height = featureHeight;
      let geometry: THREE.BufferGeometry;
      if (height) {
        geometry = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false, curveSegments: 1 });
        geometry.rotateX(-Math.PI / 2);
        geometry.translate(0, terrainBase, 0);
        confirmedBuildingCount += 1;
        bounds.max.y = Math.max(bounds.max.y, height);
      } else if (kind === 'road') {
        // A few centimetres of truthful construction depth are enough to
        // separate carriageways from the site surface without fabricating
        // terrain or a road elevation profile that the source does not have.
        geometry = new THREE.ExtrudeGeometry(shape, { depth: 0.055, bevelEnabled: false, curveSegments: 1 });
        geometry.rotateX(-Math.PI / 2);
        geometry.translate(0, terrainBase, 0);
      } else {
        geometry = new THREE.ShapeGeometry(shape, 1);
        geometry.rotateX(-Math.PI / 2);
        geometry.translate(0, terrainBase + (kind === 'water' ? 0.008 : 0.012), 0);
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

  if (bounds.isEmpty()) bounds.setFromCenterAndSize(new THREE.Vector3(), new THREE.Vector3(40, 1, 40));

  if (hasTerrain) {
    const rawGeometry = new THREE.BufferGeometry();
    rawGeometry.setAttribute('position', new THREE.Float32BufferAttribute(terrain.positions, 3));
    const geometry = mergeVertices(rawGeometry, 0.001);
    rawGeometry.dispose();
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    const position = geometry.getAttribute('position');
    const minimum = geometry.boundingBox?.min.y ?? 0;
    const span = Math.max(1, (geometry.boundingBox?.max.y ?? minimum) - minimum);
    const low = new THREE.Color(0x768e7b);
    const high = new THREE.Color(0xa8b79f);
    const colors = new Float32Array(position.count * 3);
    for (let index = 0; index < position.count; index += 1) {
      const color = low.clone().lerp(high, THREE.MathUtils.clamp((position.getY(index) - minimum) / span, 0, 1));
      color.toArray(colors, index * 3);
    }
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
      color: 0xffffff,
      vertexColors: true,
      roughness: 0.88,
      metalness: 0,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    }));
    mesh.name = 'confirmed-terrain:copernicus-glo90';
    mesh.receiveShadow = true;
    group.add(mesh);
    if (geometry.boundingBox) bounds.union(geometry.boundingBox);
  }

  // The source has no confirmed terrain. Render a bounded architectural
  // model base around the imported drawing instead of an infinite game-like
  // grid. Its deliberately shallow edge makes the local datum legible while
  // remaining honest about the missing topography.
  const contextSize = bounds.getSize(new THREE.Vector3());
  const contextCenter = bounds.getCenter(new THREE.Vector3());
  const padding = Math.max(12, Math.min(80, Math.max(contextSize.x, contextSize.z) * 0.045));
  const base = new THREE.Mesh(
    new THREE.BoxGeometry(contextSize.x + padding * 2, 0.12, contextSize.z + padding * 2),
    new THREE.MeshStandardMaterial({ color: 0xdbe5db, roughness: 0.96, metalness: 0 }),
  );
  base.name = 'context-datum-base';
  base.position.set(contextCenter.x, -0.06, contextCenter.z);
  base.receiveShadow = true;
  base.renderOrder = 0;
  if (!hasTerrain) group.add(base);
  else {
    base.geometry.dispose();
    (base.material as THREE.Material).dispose();
  }

  for (const [kind, positions] of linePositions) {
    if (!positions.length) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const material = new THREE.LineBasicMaterial({
      color: kind === 'building' ? 0x506574 : kind === 'road' ? 0x849198 : CONTEXT_COLORS[kind],
      transparent: true,
      opacity: kind === 'site_border' ? 0.88 : kind === 'building' ? 0.68 : 0.52,
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
    const material = kind === 'water'
      ? new THREE.MeshPhysicalMaterial({
        color: CONTEXT_COLORS.water,
        roughness: 0.22,
        metalness: 0,
        transmission: 0.08,
        transparent: true,
        opacity: 0.68,
        depthWrite: false,
        side: THREE.DoubleSide,
      })
      : new THREE.MeshStandardMaterial({
        color: CONTEXT_COLORS[kind],
        roughness: kind === 'road' ? 0.82 : 0.9,
        metalness: 0,
        transparent: false,
        opacity: 1,
        side: THREE.DoubleSide,
        depthWrite: true,
      });
    const mesh = new THREE.Mesh(merged, material);
    mesh.name = `context-fill:${kind}`;
    mesh.receiveShadow = kind === 'road' || kind === 'building';
    mesh.castShadow = kind === 'building' && confirmedBuildingCount > 0;
    mesh.renderOrder = 1;
    group.add(mesh);
  }

  return { group, bounds, confirmedBuildingCount, flatBuildingCount, terrainElevationAt, hasTerrain };
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
