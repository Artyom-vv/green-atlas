import * as THREE from 'three';
import type { PlantPrototype } from './plantAssets';
import { assignBuildingRoofGroups, facadeMaterial } from './facadeAppearance';
import {
  classifySceneLod,
  type SceneLod,
} from '../../model/sceneRenderContract';
import {
  disposePreparedPrototype,
  disposePreparedTexture,
  preparedMaterialTextures,
  preparedPreviewAssetLoader,
  type PreparedPreviewAsset,
  type PreparedPreviewAssetLoader,
  type PreparedPreviewMaterial,
} from './preparedPreviewAssets';

export type PreparedPreviewTree = {
  object_id: string;
  local_x: number;
  local_y: number;
  base_elevation_m: number;
  height_m: number;
  radius_m: number;
  rotation_z?: number;
  model_asset_id?: string | null;
  far_model_asset_id?: string | null;
  asset_key?: string | null;
  evidence?: unknown;
};

/** Structural subset of the immutable render-preparation response. */
export type ScenePreparedPreview = {
  id: string;
  sha256: string;
  project_id: string;
  scene_sha256: string;
  horizon_year: number;
  geometry_version: number;
  plan_version: number;
  provenance?: Record<string, unknown>;
  preview: {
    meshes: Array<{
      id: string;
      kind: string;
      vertices: number[][];
      faces: number[][];
      height_m?: number | null;
      height_status?: string | null;
      material_role?: string | null;
      material_key?: string | null;
      surface_geometry?: { type?: string; coordinates?: unknown } | null;
      surface_elevation_m?: number;
      source: 'cad_building' | 'cad_surface' | 'map_building' | 'facade_detail';
    }>;
    curbs: Array<{
      id: string;
      start_scene_xyz_m: number[];
      end_scene_xyz_m: number[];
      width_m: number;
      height_m: number;
      source_handle?: string | null;
    }>;
    context_trees: PreparedPreviewTree[];
    planned_plants?: PreparedPreviewTree[];
    assets?: PreparedPreviewAsset[];
    materials?: PreparedPreviewMaterial[];
    asset_version?: 0 | 1;
    suppressed_source_ids: string[];
  };
};

const COLORS: Record<string, number> = {
  building: 0xc0bfba,
  road: 0x62676b,
  parking_apron: 0x62676b,
  sidewalk: 0xb9b7ad,
  hard_surface: 0xb9b7ad,
  equipment: 0xb9b7ad,
  curb: 0xb6b6ac,
  lawn: 0x54813e,
  marking: 0xe1e1d4,
  water: 0x8ebdcf,
  facade_glass: 0x26343a,
  facade_frame: 0x717976,
  facade_joint: 0x8c8e87,
};

export function preparedVertexToWorld(point: number[]) {
  if (point.length !== 3 || !point.every(Number.isFinite))
    throw new Error('Invalid prepared-scene vertex');
  return new THREE.Vector3(point[0], point[2], -point[1]);
}

export function canRetainDisplayGeometry(
  preparation: ScenePreparedPreview,
  snapshot: { display_geometry_sha256?: string },
) {
  return Boolean(
    snapshot.display_geometry_sha256 &&
    preparation.provenance?.geometry_sha256 ===
      snapshot.display_geometry_sha256,
  );
}

export function preparationMatchesSnapshot(
  preparation: ScenePreparedPreview,
  snapshot: {
    project_id?: string | null;
    content_sha256?: string | null;
    horizon_year: number;
    geometry_version?: number;
    plan_version: number;
    provenance?: Record<string, unknown>;
  },
) {
  return (
    preparation.project_id === snapshot.project_id &&
    preparation.scene_sha256 === snapshot.content_sha256 &&
    preparation.horizon_year === snapshot.horizon_year &&
    preparation.geometry_version === snapshot.geometry_version &&
    preparation.plan_version === snapshot.plan_version
  );
}

export function preparedGeometryFromFaces(
  vertices: number[][],
  faces: number[][],
  tileWidthM = 1,
) {
  const points = vertices.map(preparedVertexToWorld);
  if (!Number.isFinite(tileWidthM) || tileWidthM <= 0)
    throw new Error('Invalid prepared-scene material scale');
  const positions: number[] = [];
  const uv: number[] = [];
  const indices: number[] = [];
  for (const face of faces) {
    if (
      face.length < 3 ||
      face.some(
        (index) =>
          !Number.isInteger(index) || index < 0 || index >= points.length,
      )
    )
      throw new Error('Invalid prepared-scene face');
    const a = points[face[0]],
      b = points[face[1]],
      c = points[face[2]];
    const normal = new THREE.Vector3()
      .subVectors(b, a)
      .cross(new THREE.Vector3().subVectors(c, a));
    const horizontal =
      Math.abs(normal.y) >= Math.max(Math.abs(normal.x), Math.abs(normal.z));
    for (let index = 1; index + 1 < face.length; index++)
      for (const vertexIndex of [face[0], face[index], face[index + 1]]) {
        const point = points[vertexIndex];
        indices.push(positions.length / 3);
        positions.push(...point.toArray());
        // Metre-based surface coordinates, rather than fitting a scan to each
        // polygon. Independent triangle vertices retain sharp facade edges.
        uv.push(
          (horizontal || Math.abs(normal.z) >= Math.abs(normal.x)
            ? point.x
            : -point.z) / tileWidthM,
          (horizontal ? -point.z : point.y) / tileWidthM,
        );
      }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(positions, 3),
  );
  geometry.setIndex(indices);
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}

export function preparedSurfaceGeometry(
  shape: { type?: string; coordinates?: unknown },
  elevation: number,
  tileWidth: number,
) {
  const polygons =
    shape.type === 'Polygon'
      ? [shape.coordinates]
      : shape.type === 'MultiPolygon'
        ? shape.coordinates
        : [];
  if (!Array.isArray(polygons) || !Number.isFinite(elevation))
    throw new Error('Invalid display surface');
  const shapes = polygons.map((polygon) => {
    if (!Array.isArray(polygon) || !polygon.length)
      throw new Error('Invalid display rings');
    const paths = polygon.map((ring) => {
      if (!Array.isArray(ring) || ring.length < 4)
        throw new Error('Invalid display ring');
      return ring.map((point) => {
        if (
          !Array.isArray(point) ||
          point.length < 2 ||
          !point.slice(0, 2).every(Number.isFinite)
        )
          throw new Error('Invalid display point');
        return new THREE.Vector2(point[0], point[1]);
      });
    });
    const result = new THREE.Shape(paths[0]);
    result.holes = paths.slice(1).map((points) => new THREE.Path(points));
    return result;
  });
  const geometry = new THREE.ShapeGeometry(shapes);
  geometry.rotateX(-Math.PI / 2);
  geometry.translate(0, elevation, 0);
  const position = geometry.getAttribute('position');
  const uv = geometry.getAttribute('uv');
  for (let i = 0; i < position.count; i++)
    uv.setXY(i, position.getX(i) / tileWidth, -position.getZ(i) / tileWidth);
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}

function curbGeometry(rows: ScenePreparedPreview['preview']['curbs']) {
  const vertices: number[][] = [];
  const faces: number[][] = [];
  for (const row of rows) {
    preparedVertexToWorld(row.start_scene_xyz_m);
    preparedVertexToWorld(row.end_scene_xyz_m);
    if (
      !Number.isFinite(row.width_m) ||
      row.width_m <= 0 ||
      !Number.isFinite(row.height_m) ||
      row.height_m <= 0
    )
      throw new Error(`Invalid prepared-scene curb: ${row.id}`);
    const [ax, ay, az] = row.start_scene_xyz_m;
    const [bx, by, bz] = row.end_scene_xyz_m;
    const length = Math.hypot(bx - ax, by - ay);
    if (length === 0) continue;
    const nx = ((-(by - ay) / length) * row.width_m) / 2;
    const ny = (((bx - ax) / length) * row.width_m) / 2;
    const bottom = [
      [ax + nx, ay + ny, az],
      [ax - nx, ay - ny, az],
      [bx - nx, by - ny, bz],
      [bx + nx, by + ny, bz],
    ];
    const offset = vertices.length;
    vertices.push(
      ...bottom,
      ...bottom.map(([x, y, z]) => [x, y, z + row.height_m]),
    );
    faces.push(
      [3, 2, 1, 0],
      [5, 6, 7, 4],
      [1, 5, 4, 0],
      [2, 6, 5, 1],
      [3, 7, 6, 2],
      [0, 4, 7, 3],
    );
    for (const face of faces.slice(-6))
      for (let index = 0; index < face.length; index++) face[index] += offset;
  }
  return preparedGeometryFromFaces(vertices, faces);
}

/** Owns prepared resources; editable plant instances stay in ScenePlantLayers. */
export class SceneEnvironmentLayers {
  readonly root = new THREE.Group();
  readonly bounds = new THREE.Box3();
  private generation = 0;
  private abort?: AbortController;
  private ready: Promise<void> = Promise.resolve();
  private readonly geometries = new Set<THREE.BufferGeometry>();
  private readonly materials = new Set<THREE.Material>();
  private readonly textures = new Set<THREE.Texture>();
  private readonly models = new Map<string, PlantPrototype>();
  private readonly planned = new Map<string, PreparedPreviewTree>();
  private contextTrees: PreparedPreviewTree[] = [];
  private contextModels?: THREE.Group;
  private camera?: THREE.PerspectiveCamera;
  private viewportHeight = 720;
  private bucketSignature = '';
  private readonly previousLod = new Map<string, SceneLod>();
  private readonly previousOverhead = new Map<string, boolean>();
  private readonly viewPrototypes = new Map<string, PlantPrototype>();
  private plannedViewSignature = '';

  constructor(
    private readonly loader: PreparedPreviewAssetLoader = preparedPreviewAssetLoader,
  ) {
    this.root.name = 'prepared-scene-environment';
  }

  createStaging() {
    return new SceneEnvironmentLayers(this.loader);
  }

  update(preparation?: ScenePreparedPreview) {
    this.clear();
    if (!preparation) return;
    const generation = this.generation;
    const controller = new AbortController();
    this.abort = controller;
    const group = new THREE.Group();
    group.name = `prepared:${preparation.id}`;
    group.userData.preparationId = preparation.id;
    group.userData.preparationSha256 = preparation.sha256;
    group.userData.previewQuality =
      'shared prepared geometry; local Botaniq-derived model LODs and PBR maps';
    const materials = new Map<string, THREE.MeshStandardMaterial>();
    const materialSpecs = new Map(
      (preparation.preview.materials ?? []).map((row) => [row.kind, row]),
    );
    const materialRole = (kind: string) =>
      ({
        parking_apron: 'road',
        hard_surface: 'sidewalk',
        equipment: 'sidewalk',
        glass: 'facade_glass',
        frame: 'facade_frame',
        joint: 'facade_joint',
        plinth: 'facade_plinth',
      })[kind] ?? kind;
    const materialFor = (kind: string) => {
      kind = materialRole(kind);
      let material = materials.get(kind);
      if (!material) {
        material =
          facadeMaterial(kind) ??
          new THREE.MeshStandardMaterial({
            color: COLORS[kind] ?? COLORS.sidewalk,
            roughness: 0.86,
          });
        preparedMaterialTextures(material).forEach((texture) =>
          this.textures.add(texture),
        );
        materials.set(kind, material);
        this.materials.add(material);
      }
      return material;
    };
    try {
      for (const descriptor of preparation.preview.meshes) {
        if (!descriptor.faces.length && !descriptor.surface_geometry) continue;
        const role = materialRole(
          descriptor.material_key ??
            descriptor.material_role ??
            descriptor.kind,
        );
        const geometry = descriptor.surface_geometry
          ? preparedSurfaceGeometry(
              descriptor.surface_geometry,
              descriptor.surface_elevation_m ?? 0,
              materialSpecs.get(role)?.tile_width_m ?? 1,
            )
          : preparedGeometryFromFaces(
              descriptor.vertices,
              descriptor.faces,
              materialSpecs.get(role)?.tile_width_m ?? 1,
            );
        this.geometries.add(geometry);
        const surface = materialFor(role);
        if (role === 'building') assignBuildingRoofGroups(geometry);
        const object = new THREE.Mesh(
          geometry,
          role === 'building'
            ? [surface, materialFor('building_roof')]
            : surface,
        );
        object.name = `prepared-mesh:${descriptor.id}`;
        object.userData.contextObjectId = descriptor.id;
        object.userData.source = descriptor.source;
        object.userData.heightStatus = descriptor.height_status;
        object.castShadow =
          descriptor.kind === 'building' || role === 'facade_frame';
        object.receiveShadow = true;
        group.add(object);
      }
      if (preparation.preview.curbs.length) {
        const geometry = curbGeometry(preparation.preview.curbs);
        this.geometries.add(geometry);
        const object = new THREE.Mesh(geometry, materialFor('curb'));
        object.name = 'prepared-curbs';
        object.userData.contextObjectIds = preparation.preview.curbs.map(
          (row) => row.id,
        );
        object.userData.previewQuality =
          'continuous curb envelopes; stone joints omitted';
        object.castShadow = true;
        object.receiveShadow = true;
        group.add(object);
      }
      const hasModelCatalog =
        preparation.preview.asset_version === undefined
          ? preparation.preview.assets !== undefined
          : preparation.preview.asset_version === 1;
      if (!hasModelCatalog) {
        // Older saved preparations have no resource catalog. Their explicitly
        // simplified context remains readable; new preparations require GLBs.
        this.addContextTrees(group, preparation.preview.context_trees);
      } else {
        this.contextTrees = preparation.preview.context_trees;
        this.contextModels = new THREE.Group();
        this.contextModels.name = 'prepared-context-models';
        group.add(this.contextModels);
        (preparation.preview.planned_plants ?? []).forEach((row) =>
          this.planned.set(row.object_id, row),
        );
        this.validateModelAssignments();
      }
      this.root.add(group);
      this.root.updateWorldMatrix(true, true);
      this.refreshBounds();
      if (hasModelCatalog) {
        this.ready = this.loadResources(
          preparation,
          materials,
          generation,
          controller,
        );
        // Consumers wait through whenReady(). Attach a rejection observer now
        // so a transient 3D unmount cannot produce an unhandled rejection.
        void this.ready.catch(() => undefined);
      }
    } catch (error) {
      this.clear();
      throw error;
    }
  }

  whenReady() {
    return this.ready;
  }

  /** Includes prototypes whose former LOD wrappers are no longer visible. */
  ownedMaterials(): readonly THREE.Material[] {
    return [...this.materials];
  }

  getPlannedPrototype(objectId: string, lod: SceneLod) {
    const row = this.planned.get(objectId);
    const prototype = row ? this.modelFor(row, lod) : undefined;
    return row && prototype
      ? this.prototypeForView(prototype, row, `planned:${objectId}`)
      : undefined;
  }

  getPlannedRotation(objectId: string) {
    return this.planned.get(objectId)?.rotation_z;
  }

  getPlannedElevation(objectId: string) {
    return this.planned.get(objectId)?.base_elevation_m;
  }

  /** Called when the camera changes. Context models never enter the pick scene. */
  setCamera(camera: THREE.PerspectiveCamera, viewportHeight = 720) {
    this.camera = camera;
    this.viewportHeight = Math.max(1, viewportHeight);
    const contextChanged = this.rebuildContextModels();
    const signature = [...this.planned.values()]
      .flatMap((row) => {
        const prototype = this.modelFor(row, 'far');
        return prototype
          ? [
              this.prototypeForView(prototype, row, `planned:${row.object_id}`)
                .assetKey,
            ]
          : [];
      })
      .join('|');
    const plannedChanged = signature !== this.plannedViewSignature;
    this.plannedViewSignature = signature;
    return contextChanged || plannedChanged;
  }

  private validateModelAssignments() {
    for (const row of [...this.contextTrees, ...this.planned.values()]) {
      if (
        ![
          row.local_x,
          row.local_y,
          row.base_elevation_m,
          row.height_m,
          row.radius_m,
          row.rotation_z,
        ].every(
          (value) => typeof value === 'number' && Number.isFinite(value),
        ) ||
        row.height_m <= 0 ||
        row.radius_m <= 0 ||
        !row.model_asset_id
      )
        throw new Error('Подготовка растения не содержит модели и её размеров');
    }
  }

  private ownPrototype(prototype: PlantPrototype) {
    for (const part of prototype.parts) {
      this.geometries.add(part.geometry);
      for (const material of Array.isArray(part.material)
        ? part.material
        : [part.material]) {
        this.materials.add(material);
        preparedMaterialTextures(material).forEach((texture) =>
          this.textures.add(texture),
        );
      }
    }
  }

  private async loadResources(
    preparation: ScenePreparedPreview,
    materials: Map<string, THREE.MeshStandardMaterial>,
    generation: number,
    controller: AbortController,
  ) {
    const assets = new Map(
      (preparation.preview.assets ?? []).map((row) => [row.id, row]),
    );
    const modelIds = new Set<string>();
    for (const row of [...this.contextTrees, ...this.planned.values()]) {
      modelIds.add(row.model_asset_id!);
      if (row.far_model_asset_id) modelIds.add(row.far_model_asset_id);
    }
    const specs = (preparation.preview.materials ?? []).filter((row) =>
      materials.has(row.kind),
    );
    const textureIds = new Set(specs.flatMap((row) => Object.values(row.maps)));
    const current = () =>
      generation === this.generation && !controller.signal.aborted;
    const required = (id: string, kind: PreparedPreviewAsset['kind']) => {
      const asset = assets.get(id);
      if (!asset || asset.kind !== kind)
        throw new Error(
          'Подготовка сцены ссылается на отсутствующий ресурс оформления',
        );
      return asset;
    };
    try {
      const loadedTextures = new Map<string, THREE.Texture>();
      await Promise.all([
        ...[...modelIds].map(async (id) => {
          const prototype = await this.loader.model(
            required(id, 'model'),
            controller.signal,
          );
          if (!current()) {
            disposePreparedPrototype(prototype);
            return;
          }
          this.models.set(id, prototype);
          this.ownPrototype(prototype);
        }),
        ...[...textureIds].map(async (id) => {
          const texture = await this.loader.texture(
            required(id, 'texture'),
            controller.signal,
          );
          if (!current()) {
            disposePreparedTexture(texture);
            return;
          }
          loadedTextures.set(id, texture);
          this.textures.add(texture);
        }),
      ]);
      if (!current()) return;
      for (const spec of specs) {
        const material = materials.get(spec.kind)!;
        const color = spec.maps.diffuse
          ? loadedTextures.get(spec.maps.diffuse)
          : undefined;
        const normal = spec.maps.normal_gl
          ? loadedTextures.get(spec.maps.normal_gl)
          : undefined;
        const roughness = spec.maps.roughness
          ? loadedTextures.get(spec.maps.roughness)
          : undefined;
        if (color) {
          color.colorSpace = THREE.SRGBColorSpace;
          material.map = color;
          material.color.set(0xffffff);
        }
        if (normal) {
          normal.colorSpace = THREE.NoColorSpace;
          material.normalMap = normal;
        }
        if (roughness) {
          roughness.colorSpace = THREE.NoColorSpace;
          material.roughnessMap = roughness;
          material.roughness = 1;
        }
        material.normalScale.setScalar(spec.normal_strength);
        material.needsUpdate = true;
      }
      this.rebuildContextModels();
      this.root.updateWorldMatrix(true, true);
      this.refreshBounds();
    } catch (error) {
      if (!current()) return;
      controller.abort();
      throw error instanceof Error
        ? error
        : new Error('Не удалось подготовить оформление сцены');
    }
  }

  private modelFor(row: PreparedPreviewTree, lod: SceneLod) {
    const id =
      lod === 'near'
        ? row.model_asset_id
        : (row.far_model_asset_id ?? row.model_asset_id);
    return id ? this.models.get(id) : undefined;
  }

  private refreshBounds() {
    this.bounds.makeEmpty();
    for (const group of this.root.children)
      for (const object of group.children)
        if (object !== this.contextModels) this.bounds.expandByObject(object);
    // A filtered top view is a flat capture, not a change to the accepted tree
    // envelope. Its transparent image corners must not expand that envelope
    // after rotation either. Navigation retains the frozen dimensions.
    for (const row of this.contextTrees)
      this.bounds.union(
        new THREE.Box3(
          new THREE.Vector3(
            row.local_x - row.radius_m,
            row.base_elevation_m,
            -row.local_y - row.radius_m,
          ),
          new THREE.Vector3(
            row.local_x + row.radius_m,
            row.base_elevation_m + row.height_m,
            -row.local_y + row.radius_m,
          ),
        ),
      );
  }

  private prototypeForView(
    prototype: PlantPrototype,
    row: PreparedPreviewTree,
    key: string,
  ) {
    // Older whole-tree exports have vertical captures only. Preserve them;
    // ordinary near geometry is never filtered by a distant-view policy.
    if (
      !prototype.parts.some((part) => part.overheadOnly === true) ||
      !prototype.parts.some((part) => part.overheadOnly === false)
    )
      return prototype;
    let overhead = false;
    if (this.camera) {
      const horizontal = Math.hypot(
        this.camera.position.x - row.local_x,
        this.camera.position.z + row.local_y,
      );
      const elevation = THREE.MathUtils.radToDeg(
        Math.atan2(
          this.camera.position.y -
            (row.base_elevation_m + row.height_m * 0.625),
          horizontal,
        ),
      );
      const previous = this.previousOverhead.get(key);
      overhead =
        previous === undefined
          ? elevation > 35
          : previous
            ? elevation >= 32
            : elevation > 38;
    }
    this.previousOverhead.set(key, overhead);
    const assetKey = `${prototype.assetKey}:view-${overhead ? 'overhead' : 'street'}`;
    let view = this.viewPrototypes.get(assetKey);
    if (!view) {
      view = {
        ...prototype,
        assetKey,
        parts: prototype.parts.filter(
          (part) =>
            part.overheadOnly === undefined || part.overheadOnly === overhead,
        ),
      };
      this.viewPrototypes.set(assetKey, view);
    }
    return view;
  }

  private rebuildContextModels() {
    if (!this.contextModels || !this.models.size) return false;
    const camera = this.camera;
    let frustum: THREE.Frustum | undefined;
    if (camera) {
      camera.updateMatrixWorld();
      frustum = new THREE.Frustum().setFromProjectionMatrix(
        new THREE.Matrix4().multiplyMatrices(
          camera.projectionMatrix,
          camera.matrixWorldInverse,
        ),
      );
    }
    const buckets = new Map<
      string,
      { prototype: PlantPrototype; rows: PreparedPreviewTree[] }
    >();
    for (const row of this.contextTrees) {
      const center = new THREE.Vector3(
        row.local_x,
        row.base_elevation_m + row.height_m / 2,
        -row.local_y,
      );
      const radius = Math.hypot(row.radius_m, row.height_m / 2);
      if (
        frustum &&
        !frustum.intersectsSphere(new THREE.Sphere(center, radius))
      )
        continue;
      const distance = camera
        ? Math.max(0.1, camera.position.distanceTo(center))
        : Infinity;
      const diameter = camera
        ? (row.radius_m * this.viewportHeight) /
          (distance * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)))
        : 0;
      const lod = classifySceneLod(
        diameter,
        this.previousLod.get(row.object_id),
      );
      this.previousLod.set(row.object_id, lod);
      const sourcePrototype = this.modelFor(row, lod);
      if (!sourcePrototype) continue;
      const prototype = this.prototypeForView(
        sourcePrototype,
        row,
        `context:${row.object_id}`,
      );
      let bucket = buckets.get(prototype.assetKey);
      if (!bucket) {
        bucket = { prototype, rows: [] };
        buckets.set(prototype.assetKey, bucket);
      }
      bucket.rows.push(row);
    }
    const signature = [...buckets]
      .map(
        ([key, bucket]) =>
          `${key}:${bucket.rows.map((row) => row.object_id).join(',')}`,
      )
      .join('|');
    if (signature === this.bucketSignature) return false;
    this.bucketSignature = signature;
    this.contextModels.children.forEach((object) => {
      if (object instanceof THREE.InstancedMesh) object.dispose();
    });
    this.contextModels.clear();
    const rotation = new THREE.Quaternion();
    for (const [key, { prototype, rows }] of buckets)
      for (let partIndex = 0; partIndex < prototype.parts.length; partIndex++) {
        const part = prototype.parts[partIndex];
        const object = new THREE.InstancedMesh(
          part.geometry,
          part.material,
          rows.length,
        );
        rows.forEach((row, index) => {
          rotation.setFromAxisAngle(THREE.Object3D.DEFAULT_UP, row.rotation_z!);
          object.setMatrixAt(
            index,
            new THREE.Matrix4().compose(
              new THREE.Vector3(
                row.local_x,
                row.base_elevation_m,
                -row.local_y,
              ),
              rotation,
              new THREE.Vector3(row.radius_m, row.height_m, row.radius_m),
            ),
          );
        });
        object.name = `prepared-context-model:${key}:${partIndex}`;
        object.userData.contextObjectIds = rows.map((row) => row.object_id);
        object.userData.previewQuality =
          'same frozen Botaniq source variant; reduced mesh LOD';
        object.castShadow = part.castShadow;
        object.receiveShadow = (
          Array.isArray(part.material) ? part.material : [part.material]
        ).some((material) => !(material instanceof THREE.MeshBasicMaterial));
        object.computeBoundingBox();
        object.computeBoundingSphere();
        this.contextModels.add(object);
      }
    this.root.updateWorldMatrix(true, true);
    this.refreshBounds();
    return true;
  }

  private addContextTrees(
    group: THREE.Group,
    rows: ScenePreparedPreview['preview']['context_trees'],
  ) {
    if (!rows.length) return;
    for (const row of rows)
      if (
        ![
          row.local_x,
          row.local_y,
          row.base_elevation_m,
          row.height_m,
          row.radius_m,
        ].every(Number.isFinite) ||
        row.height_m <= 0 ||
        row.radius_m <= 0
      )
        throw new Error(`Invalid prepared-scene tree: ${row.object_id}`);
    const crown = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1, 12, 8),
      new THREE.MeshStandardMaterial({ color: 0x437338, roughness: 0.94 }),
      rows.length,
    );
    const trunk = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(1, 1, 1, 6),
      new THREE.MeshStandardMaterial({ color: 0x66513c, roughness: 0.96 }),
      rows.length,
    );
    for (const object of [crown, trunk]) {
      this.geometries.add(object.geometry);
      this.materials.add(object.material as THREE.Material);
    }
    const matrix = new THREE.Matrix4();
    const rotation = new THREE.Quaternion();
    const position = new THREE.Vector3();
    const scale = new THREE.Vector3();
    rows.forEach((row, index) => {
      crown.setMatrixAt(
        index,
        matrix.compose(
          position.set(
            row.local_x,
            row.base_elevation_m + row.height_m * 0.625,
            -row.local_y,
          ),
          rotation,
          scale.set(row.radius_m, row.height_m * 0.375, row.radius_m),
        ),
      );
      trunk.setMatrixAt(
        index,
        matrix.compose(
          position.set(
            row.local_x,
            row.base_elevation_m + row.height_m * 0.225,
            -row.local_y,
          ),
          rotation,
          scale.set(
            row.radius_m * 0.08,
            row.height_m * 0.45,
            row.radius_m * 0.08,
          ),
        ),
      );
    });
    for (const [object, part] of [
      [crown, 'crowns'],
      [trunk, 'trunks'],
    ] as const) {
      object.name = `prepared-context-trees:${part}`;
      object.userData.contextObjectIds = rows.map((row) => row.object_id);
      object.userData.previewQuality =
        'parametric context tree proxy; prepared positions and dimensions';
      object.castShadow = true;
      object.receiveShadow = true;
      object.instanceMatrix.needsUpdate = true;
      object.computeBoundingBox();
      object.computeBoundingSphere();
      group.add(object);
    }
  }

  clear() {
    this.generation++;
    this.abort?.abort();
    this.abort = undefined;
    this.ready = Promise.resolve();
    this.root.traverse((object) => {
      if (object instanceof THREE.InstancedMesh) object.dispose();
    });
    this.geometries.forEach((geometry) => geometry.dispose());
    this.materials.forEach((material) => material.dispose());
    this.textures.forEach(disposePreparedTexture);
    this.geometries.clear();
    this.materials.clear();
    this.textures.clear();
    this.models.clear();
    this.planned.clear();
    this.contextTrees = [];
    this.contextModels = undefined;
    this.bucketSignature = '';
    this.previousLod.clear();
    this.previousOverhead.clear();
    this.viewPrototypes.clear();
    this.plannedViewSignature = '';
    this.root.clear();
    this.bounds.makeEmpty();
  }

  dispose() {
    this.clear();
  }
}
