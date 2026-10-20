import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import {
  SceneEnvironmentLayers,
  preparationMatchesSnapshot,
  preparedVertexToWorld,
  preparedGeometryFromFaces,
  type ScenePreparedPreview,
} from './SceneEnvironmentLayers';
import { SceneController } from './SceneController';
import {
  plantMatrix,
  ScenePlantLayers,
  type PlantInstance,
} from './ScenePlantLayers';
import type { PlantPrototype } from './plantAssets';
import type { PreparedPreviewAssetLoader } from './preparedPreviewAssets';

function preparation(): ScenePreparedPreview {
  return {
    id: 'preparation-one',
    sha256: 'prepared-sha',
    project_id: 'project-one',
    scene_sha256: 'scene-sha',
    horizon_year: 0,
    geometry_version: 3,
    plan_version: 4,
    preview: {
      meshes: [],
      curbs: [],
      context_trees: [],
      suppressed_source_ids: [],
    },
  };
}

function nativePreparation() {
  const p = preparation();
  p.preview.assets = ['near', 'far'].map((lod) => ({
    id: `tree-${lod}`,
    kind: 'model' as const,
    lod: lod as 'near' | 'far',
    sha256: '0'.repeat(64),
    url: `/api/assets/tree-${lod}`,
  }));
  p.preview.context_trees = [
    {
      object_id: 'existing-one',
      local_x: 7,
      local_y: 11,
      base_elevation_m: 120,
      height_m: 12,
      radius_m: 3.5,
      rotation_z: Math.PI / 2,
      model_asset_id: 'tree-near',
      far_model_asset_id: 'tree-far',
    },
  ];
  return p;
}

function prototype(id: string, lod: 'near' | 'far' = 'near'): PlantPrototype {
  const geometry = new THREE.BoxGeometry(2, 1, 2);
  geometry.translate(0, 0.5, 0);
  return {
    assetKey: `prepared:${id}`,
    lod,
    nominalHeightM: 1,
    nominalRadiusM: 1,
    parts: [
      {
        geometry,
        material: new THREE.MeshStandardMaterial(),
        billboard: false,
        castShadow: lod !== 'far',
      },
    ],
  };
}

function nativeLoader(): PreparedPreviewAssetLoader {
  return {
    model: vi.fn(async (asset) => prototype(asset.id, asset.lod)),
    texture: vi.fn(async () => new THREE.Texture()),
  };
}

function wholeTreeViews(id: string): PlantPrototype {
  const vertical = new THREE.PlaneGeometry(2, 1);
  vertical.translate(0, 0.5, 0);
  const top = new THREE.PlaneGeometry(2, 2);
  top.rotateX(-Math.PI / 2);
  top.translate(0, 0.625, 0);
  const streetMaterial = new THREE.MeshBasicMaterial({
    name: 'whole-tree Botaniq multiview 45',
  });
  const topMaterial = new THREE.MeshBasicMaterial({
    name: 'whole-tree Botaniq multiview top 90',
  });
  return {
    assetKey: id,
    lod: 'far',
    nominalHeightM: 1,
    nominalRadiusM: 1,
    parts: [
      ...Array.from({ length: 4 }, () => ({
        geometry: vertical,
        material: streetMaterial,
        castShadow: false,
        billboard: false,
        overheadOnly: false,
      })),
      {
        geometry: top,
        material: topMaterial,
        castShadow: false,
        billboard: false,
        overheadOnly: true,
      },
    ],
  };
}

describe('immutable prepared scene in Three', () => {
  it('maps asymmetric CAD coordinates and absolute Z without a second translation', () => {
    expect(preparedVertexToWorld([13, -29, 151]).toArray()).toEqual([
      13, 151, 29,
    ]);
    const p = preparation();
    p.preview.meshes.push({
      id: 'tower',
      kind: 'building',
      source: 'cad_building',
      vertices: [
        [13, -29, 151],
        [16, -29, 151],
        [13, -25, 451],
      ],
      faces: [[0, 1, 2]],
      height_m: 300,
    });
    const original = structuredClone(p);
    const layers = new SceneEnvironmentLayers();
    layers.update(p);
    expect(layers.bounds.min.toArray()).toEqual([13, 151, 25]);
    expect(layers.bounds.max.toArray()).toEqual([16, 451, 29]);
    expect(p).toEqual(original);
    layers.dispose();
  });

  it('uses the supplied roof triangles, preserving a courtyard opening', () => {
    const p = preparation();
    p.preview.meshes.push({
      id: 'courtyard',
      kind: 'building',
      source: 'map_building',
      vertices: [
        [0, 0, 6],
        [10, 0, 6],
        [10, 10, 6],
        [0, 10, 6],
        [3, 3, 6],
        [7, 3, 6],
        [7, 7, 6],
        [3, 7, 6],
      ],
      faces: [
        [0, 1, 5],
        [0, 5, 4],
        [1, 2, 6],
        [1, 6, 5],
        [2, 3, 7],
        [2, 7, 6],
        [3, 0, 4],
        [3, 4, 7],
      ],
    });
    const layers = new SceneEnvironmentLayers();
    layers.update(p);
    const object = layers.root.getObjectByName('prepared-mesh:courtyard')!;
    const ray = new THREE.Raycaster(
      new THREE.Vector3(5, 10, -5),
      new THREE.Vector3(0, -1, 0),
    );
    expect(ray.intersectObject(object)).toHaveLength(0);
    ray.set(new THREE.Vector3(1, 10, -1), new THREE.Vector3(0, -1, 0));
    expect(ray.intersectObject(object).length).toBeGreaterThan(0);
    layers.dispose();
  });

  it('keeps curb widths, endpoint grade and outward top normals', () => {
    const p = preparation();
    p.preview.curbs.push({
      id: 'curb',
      start_scene_xyz_m: [0, 0, 0.1],
      end_scene_xyz_m: [5, 0, 0.2],
      width_m: 0.15,
      height_m: 0.18,
    });
    const layers = new SceneEnvironmentLayers();
    layers.update(p);
    const mesh = layers.root.getObjectByName('prepared-curbs') as THREE.Mesh;
    expect(layers.bounds.min.x).toBeCloseTo(0);
    expect(layers.bounds.max.x).toBeCloseTo(5);
    expect(layers.bounds.min.y).toBeCloseTo(0.1);
    expect(layers.bounds.max.y).toBeCloseTo(0.38);
    expect(layers.bounds.min.z).toBeCloseTo(-0.075);
    expect(layers.bounds.max.z).toBeCloseTo(0.075);
    const index = mesh.geometry.index!;
    const positions = mesh.geometry.getAttribute('position');
    const a = new THREE.Vector3().fromBufferAttribute(positions, index.getX(6));
    const b = new THREE.Vector3().fromBufferAttribute(positions, index.getX(7));
    const c = new THREE.Vector3().fromBufferAttribute(positions, index.getX(8));
    expect(b.sub(a).cross(c.sub(a)).normalize().y).toBeGreaterThan(0.99);
    layers.dispose();
  });

  it('gives existing trees their prepared envelope and never planned picking IDs', () => {
    const p = preparation();
    p.preview.context_trees.push({
      object_id: 'existing-one',
      local_x: 7,
      local_y: 11,
      base_elevation_m: 120,
      height_m: 12,
      radius_m: 3.5,
    });
    const layers = new SceneEnvironmentLayers();
    layers.update(p);
    layers.bounds.min
      .toArray()
      .forEach((value, index) =>
        expect(value).toBeCloseTo([3.5, 120, -14.5][index], 5),
      );
    layers.bounds.max
      .toArray()
      .forEach((value, index) =>
        expect(value).toBeCloseTo([10.5, 132, -7.5][index], 5),
      );
    const crown = layers.root.getObjectByName(
      'prepared-context-trees:crowns',
    ) as THREE.InstancedMesh;
    expect(crown.count).toBe(1);
    expect(crown.userData.contextObjectIds).toEqual(['existing-one']);
    expect(crown.userData.objectIds).toBeUndefined();
    expect(crown.userData.plantBatch).toBeUndefined();
    expect(crown.userData.previewQuality).toContain('proxy');
    layers.dispose();
  });

  it('reads an explicit legacy preparation with an empty resource catalog', async () => {
    const p = nativePreparation();
    p.preview.asset_version = 0;
    p.preview.assets = [];
    delete p.preview.context_trees[0].model_asset_id;
    delete p.preview.context_trees[0].far_model_asset_id;
    const loader = nativeLoader();
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(p);
    await layers.whenReady();
    expect(
      layers.root.getObjectByName('prepared-context-trees:crowns'),
    ).toBeDefined();
    expect(loader.model).not.toHaveBeenCalled();
    layers.dispose();
  });

  it('releases GPU geometry, materials and instance buffers when a preparation clears', () => {
    const p = preparation();
    p.preview.context_trees.push({
      object_id: 'existing',
      local_x: 0,
      local_y: 0,
      base_elevation_m: 0,
      height_m: 12,
      radius_m: 3.5,
    });
    const layers = new SceneEnvironmentLayers();
    layers.update(p);
    const mesh = layers.root.getObjectByName(
      'prepared-context-trees:crowns',
    ) as THREE.InstancedMesh;
    const geometry = vi.spyOn(mesh.geometry, 'dispose');
    const material = vi.spyOn(mesh.material as THREE.Material, 'dispose');
    const buffer = vi.spyOn(mesh, 'dispose');
    layers.update();
    expect(geometry).toHaveBeenCalledOnce();
    expect(material).toHaveBeenCalledOnce();
    expect(buffer).toHaveBeenCalledOnce();
    expect(layers.root.children).toHaveLength(0);
    expect(layers.bounds.isEmpty()).toBe(true);
    layers.dispose();
    expect(geometry).toHaveBeenCalledOnce();
  });

  it('rejects a preparation from another project, source scene or revision', () => {
    const p = preparation();
    const snapshot = {
      project_id: p.project_id,
      content_sha256: p.scene_sha256,
      horizon_year: 0,
      geometry_version: 3,
      plan_version: 4,
    };
    expect(preparationMatchesSnapshot(p, snapshot)).toBe(true);
    for (const changed of [
      { project_id: 'other' },
      { content_sha256: 'other' },
      { horizon_year: 40 },
      { geometry_version: 4 },
      { plan_version: 5 },
    ])
      expect(preparationMatchesSnapshot(p, { ...snapshot, ...changed })).toBe(
        false,
      );
  });

  it('clears the controller old environment on a stale response without changing plant picking', async () => {
    const p = preparation();
    const layers = new SceneEnvironmentLayers();
    layers.update(p);
    const context = {
      disposed: false,
      environmentGeneration: 0,
      environmentLayers: layers,
      environmentPreparation: p,
      snapshot: {
        project_id: 'other',
        content_sha256: 'other',
        horizon_year: 0,
        geometry_version: 3,
        plan_version: 4,
      },
      detachCsmMaterials: vi.fn(),
      rebuildContext: vi.fn(),
      rebuildPlants: vi.fn(),
      rebuildOutlines: vi.fn(),
      requestRender: vi.fn(),
      clearPreparedEnvironment:
        SceneController.prototype['clearPreparedEnvironment'],
      plants: { pickIds: ['planned-one'], rebuild: vi.fn() },
    };
    expect(
      await SceneController.prototype.updateEnvironment.call(
        context as unknown as SceneController,
        p,
      ),
    ).toBe(false);
    expect(layers.root.children).toHaveLength(0);
    expect(context.environmentPreparation).toBeUndefined();
    expect(context.plants.pickIds).toEqual(['planned-one']);
    expect(context.rebuildContext).toHaveBeenCalledOnce();
    layers.dispose();
  });

  it('suppresses only addressed source objects, retaining other geometry of the same kind', () => {
    const p = preparation();
    p.preview.suppressed_source_ids = ['replaced-road'];
    const features = ['replaced-road', 'retained-road'].map((id, index) => ({
      feature_id: id,
      kind: 'road',
      geometry: {
        type: 'LineString',
        coordinates: [
          [index * 10, 0],
          [index * 10, 5],
        ],
      },
    }));
    const original = structuredClone(features);
    const context = {
      snapshot: {
        coordinate_origin: [0, 0],
        objects: [],
        context_features: features,
      },
      environmentPreparation: p,
      environmentLayers: new SceneEnvironmentLayers(),
      contextRoot: new THREE.Group(),
      navigation: {
        bounds: new THREE.Box3(),
        setBasis: vi.fn(),
        boundsForObjects: () => new THREE.Box3(),
      },
      detachCsmMaterials: vi.fn(),
      configureCsmMaterials: vi.fn(),
      disposeContext: SceneController.prototype['disposeContext'],
    };
    SceneController.prototype['rebuildContext'].call(
      context as unknown as SceneController,
    );
    const lines = context.contextRoot.getObjectByName(
      'context-lines:road',
    ) as THREE.LineSegments;
    expect(lines.geometry.getAttribute('position').count).toBe(2);
    expect(lines.geometry.getAttribute('position').getX(0)).toBe(10);
    expect(features).toEqual(original);
    context.environmentLayers.dispose();
    SceneController.prototype['disposeContext'].call(
      context as unknown as SceneController,
    );
  });

  it('retains metric texture coordinates across separate polygons and hard architecture corners', () => {
    const geometry = preparedGeometryFromFaces(
      [
        [0, 0, 0],
        [4, 0, 0],
        [4, 6, 0],
        [0, 6, 0],
        [0, 0, 8],
        [4, 0, 8],
      ],
      [
        [0, 1, 2, 3],
        [0, 4, 5, 1],
      ],
      2,
    );
    const uv = geometry.getAttribute('uv');
    expect([uv.getX(0), uv.getY(0)]).toEqual([0, 0]);
    expect([uv.getX(1), uv.getY(1)]).toEqual([2, 0]);
    expect([uv.getX(2), uv.getY(2)]).toEqual([2, 3]);
    const normals = geometry.getAttribute('normal');
    expect(normals.getY(0)).toBeCloseTo(1);
    expect(Math.abs(normals.getZ(6))).toBeCloseTo(1);
    expect(normals.getY(6)).toBeCloseTo(0);
    geometry.dispose();
  });

  it('loads the frozen native variant with exact rotation, dimensions and separate context metadata', async () => {
    const p = nativePreparation();
    p.preview.planned_plants = [
      { ...p.preview.context_trees[0], object_id: 'planned-one' },
    ];
    const loader = nativeLoader();
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(p);
    expect(
      layers.root.getObjectByName('prepared-context-trees:crowns'),
    ).toBeUndefined();
    expect(layers.getPlannedPrototype('planned-one', 'near')).toBeUndefined();
    await layers.whenReady();
    const model = layers.root.getObjectByName('prepared-context-models')!
      .children[0] as THREE.InstancedMesh;
    const matrix = new THREE.Matrix4();
    model.getMatrixAt(0, matrix);
    const position = new THREE.Vector3(),
      rotation = new THREE.Quaternion(),
      scale = new THREE.Vector3();
    matrix.decompose(position, rotation, scale);
    expect(position.toArray()).toEqual([7, 120, -11]);
    scale
      .toArray()
      .forEach((value, index) =>
        expect(value).toBeCloseTo([3.5, 12, 3.5][index]),
      );
    expect(new THREE.Vector3(1, 0, 0).applyQuaternion(rotation).z).toBeCloseTo(
      -1,
    );
    expect(model.userData.contextObjectIds).toEqual(['existing-one']);
    expect(model.userData.objectIds).toBeUndefined();
    expect(model.userData.plantBatch).toBeUndefined();
    expect(layers.getPlannedPrototype('planned-one', 'near')!.assetKey).toBe(
      'prepared:tree-near',
    );
    expect(layers.getPlannedRotation('planned-one')).toBe(Math.PI / 2);
    expect(loader.model).toHaveBeenCalledTimes(2);
    layers.dispose();
  });

  it('uses prepared rotation for an editable plant without changing its source coordinates', () => {
    const object = {
      object_id: 'planned',
      kind: 'tree',
      local_x: 13,
      local_y: -29,
      canopy_radius_max_m: 3,
      height_max_m: 12,
    } as Parameters<typeof plantMatrix>[0];
    const original = structuredClone(object);
    const model = prototype('native');
    const matrix = plantMatrix(object, model, 151, Math.PI / 2);
    const point = new THREE.Vector3(1, 0, 0).applyMatrix4(matrix);
    expect(point.x).toBeCloseTo(13);
    expect(point.y).toBe(151);
    expect(point.z).toBeCloseTo(26);
    expect(object).toEqual(original);
    model.parts[0].geometry.dispose();
    (model.parts[0].material as THREE.Material).dispose();
  });

  it('changes context LOD by projected crown size and removes invisible instances', async () => {
    const layers = new SceneEnvironmentLayers(nativeLoader());
    layers.update(nativePreparation());
    await layers.whenReady();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.04, 5000);
    camera.position.set(7, 126, 15);
    camera.lookAt(7, 126, -11);
    expect(layers.setCamera(camera, 720)).toBe(true);
    expect(
      layers.root.getObjectByName('prepared-context-models')!.children[0].name,
    ).toContain('tree-near');
    expect(layers.setCamera(camera, 720)).toBe(false);
    camera.position.set(7, 126, 1200);
    camera.lookAt(7, 126, -11);
    expect(layers.setCamera(camera, 720)).toBe(true);
    expect(
      layers.root.getObjectByName('prepared-context-models')!.children[0].name,
    ).toContain('tree-far');
    camera.lookAt(7, 126, 2000);
    expect(layers.setCamera(camera, 720)).toBe(true);
    expect(
      layers.root.getObjectByName('prepared-context-models')!.children,
    ).toHaveLength(0);
    layers.dispose();
  });

  it('keeps baked whole-tree context cards free of runtime shadows', async () => {
    const loader = nativeLoader();
    loader.model = vi.fn(async (asset) => {
      const model = prototype(asset.id, asset.lod ?? 'near');
      (model.parts[0].material as THREE.Material).dispose();
      model.parts[0].material = new THREE.MeshBasicMaterial({
        name: 'whole-tree Botaniq multiview',
      });
      model.parts[0].castShadow = false;
      return model;
    });
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(nativePreparation());
    await layers.whenReady();
    const mesh = layers.root.getObjectByName('prepared-context-models')!
      .children[0] as THREE.Mesh;
    expect(mesh.castShadow).toBe(false);
    expect(mesh.receiveShadow).toBe(false);
    layers.dispose();
  });

  it('shows top captures only above the crown and keeps declared tree bounds and planned picking IDs', async () => {
    const p = nativePreparation();
    p.preview.planned_plants = [
      { ...p.preview.context_trees[0], object_id: 'planned-one' },
    ];
    const loader = nativeLoader();
    loader.model = vi.fn(async (asset) =>
      asset.lod === 'far' ? wholeTreeViews(asset.id) : prototype(asset.id),
    );
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(p);
    await layers.whenReady();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.04, 5000);
    const pointAtElevation = (angle: number) => {
      camera.position.set(
        7,
        127.5 + Math.tan(THREE.MathUtils.degToRad(angle)) * 111,
        100,
      );
      camera.lookAt(7, 126, -11);
      return layers.setCamera(camera, 720);
    };
    pointAtElevation(10);
    const street = layers.getPlannedPrototype('planned-one', 'far')!;
    expect(street.parts).toHaveLength(4);
    expect(street.parts.every((part) => part.overheadOnly === false)).toBe(
      true,
    );
    const bounds = layers.bounds.clone();
    expect(
      layers.root.getObjectByName('prepared-context-models')!.children,
    ).toHaveLength(4);
    pointAtElevation(60);
    const top = layers.getPlannedPrototype('planned-one', 'far')!;
    expect(top.parts).toHaveLength(1);
    expect(top.parts[0].overheadOnly).toBe(true);
    expect(top.assetKey).not.toBe(street.assetKey);
    expect(
      layers.root.getObjectByName('prepared-context-models')!.children,
    ).toHaveLength(1);
    expect(layers.bounds.equals(bounds)).toBe(true);
    expect(top.nominalHeightM).toBe(street.nominalHeightM);
    expect(top.nominalRadiusM).toBe(street.nominalRadiusM);
    const object = {
      object_id: 'planned-one',
      kind: 'tree',
      local_x: 7,
      local_y: 11,
      height_max_m: 12,
      canopy_radius_max_m: 3.5,
    } as Parameters<typeof plantMatrix>[0];
    const context = {
      plantRoot: new THREE.Group(),
      pickRoot: new THREE.Group(),
      pickIds: [] as string[],
      whiteAlphaTexture: new THREE.Texture(),
      matrixForPart: (item: PlantInstance) => item.matrix,
    };
    for (const model of [street, top]) {
      ScenePlantLayers.prototype.rebuild.call(
        context as unknown as ScenePlantLayers,
        [
          {
            object,
            prototype: model,
            lod: 'far',
            matrix: plantMatrix(object, model, 120, Math.PI / 2),
          },
        ],
      );
      expect(context.pickIds).toEqual(['planned-one']);
      expect(
        context.plantRoot.children.every(
          (mesh) => mesh.userData.objectIds[0] === 'planned-one',
        ),
      ).toBe(true);
    }
    ScenePlantLayers.prototype.rebuild.call(
      context as unknown as ScenePlantLayers,
      [],
    );
    context.whiteAlphaTexture.dispose();
    layers.dispose();
  });

  it('uses per-tree view elevation with hysteresis and signals planned-only mode changes', async () => {
    const p = nativePreparation();
    p.preview.planned_plants = [
      { ...p.preview.context_trees[0], object_id: 'planned-one' },
    ];
    p.preview.context_trees = [];
    const loader = nativeLoader();
    loader.model = vi.fn(async (asset) =>
      asset.lod === 'far' ? wholeTreeViews(asset.id) : prototype(asset.id),
    );
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(p);
    await layers.whenReady();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.04, 5000);
    const setElevation = (angle: number) => {
      camera.position.set(
        7,
        127.5 + Math.tan(THREE.MathUtils.degToRad(angle)) * 111,
        100,
      );
      camera.lookAt(7, 126, -11);
      return layers.setCamera(camera, 720);
    };
    expect(setElevation(34)).toBe(true);
    const street = layers.getPlannedPrototype('planned-one', 'far')!;
    expect(setElevation(36)).toBe(false);
    expect(layers.getPlannedPrototype('planned-one', 'far')).toBe(street);
    expect(setElevation(39)).toBe(true);
    const top = layers.getPlannedPrototype('planned-one', 'far')!;
    expect(top.parts).toHaveLength(1);
    expect(setElevation(34)).toBe(false);
    expect(layers.getPlannedPrototype('planned-one', 'far')).toBe(top);
    expect(setElevation(31)).toBe(true);
    expect(layers.getPlannedPrototype('planned-one', 'far')).toBe(street);
    expect(
      layers.getPlannedPrototype('planned-one', 'near')!.parts[0].overheadOnly,
    ).toBeUndefined();
    layers.dispose();
  });

  it('keeps distant crowns in street mode when nearby crowns are viewed from above', async () => {
    const p = nativePreparation();
    p.preview.context_trees = [50, 200].map((local_y, index) => ({
      ...p.preview.context_trees[0],
      object_id: index === 0 ? 'close-tree' : 'far-tree',
      local_x: 0,
      local_y,
      base_elevation_m: 0,
    }));
    const loader = nativeLoader();
    loader.model = vi.fn(async (asset) =>
      asset.lod === 'far' ? wholeTreeViews(asset.id) : prototype(asset.id),
    );
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(p);
    await layers.whenReady();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.04, 5000);
    camera.position.set(0, 60, 0);
    camera.lookAt(0, 10, -100);
    layers.setCamera(camera, 720);
    const objects = layers.root.getObjectByName(
      'prepared-context-models',
    )!.children;
    const close = objects.filter((object) =>
      object.userData.contextObjectIds.includes('close-tree'),
    );
    const far = objects.filter((object) =>
      object.userData.contextObjectIds.includes('far-tree'),
    );
    expect(close).toHaveLength(1);
    expect(close[0].name).toContain('view-overhead');
    expect(far).toHaveLength(4);
    expect(far.every((object) => object.name.includes('view-street'))).toBe(
      true,
    );
    layers.dispose();
  });

  it('rejects model load failures without silently substituting a spherical crown', async () => {
    const loader = nativeLoader();
    loader.model = vi.fn().mockRejectedValue(new Error('GLB resource missing'));
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(nativePreparation());
    await expect(layers.whenReady()).rejects.toThrow('GLB resource missing');
    expect(
      layers.root.getObjectByName('prepared-context-trees:crowns'),
    ).toBeUndefined();
    layers.dispose();
  });

  it('disposes a late model after cancellation without attaching it to a newer preparation', async () => {
    let resolve!: (value: PlantPrototype) => void;
    const pending = new Promise<PlantPrototype>((done) => {
      resolve = done;
    });
    const loader = nativeLoader();
    loader.model = vi.fn().mockReturnValue(pending);
    const p = nativePreparation();
    p.preview.context_trees[0].far_model_asset_id = undefined;
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(p);
    const previous = layers.whenReady();
    const late = prototype('late');
    const dispose = vi.spyOn(late.parts[0].geometry, 'dispose');
    layers.update(preparation());
    resolve(late);
    await previous;
    expect(dispose).toHaveBeenCalledOnce();
    expect(
      layers.root.getObjectByName('prepared-context-models'),
    ).toBeUndefined();
    expect(layers.root.children[0].name).toBe('prepared:preparation-one');
    layers.dispose();
  });

  it('applies frozen material maps with correct color spaces, metric scale and one-time disposal', async () => {
    const p = preparation();
    p.preview.assets = ['color', 'normal', 'rough'].map((id) => ({
      id,
      kind: 'texture',
      url: `/api/assets/${id}`,
      sha256: '0'.repeat(64),
    }));
    p.preview.materials = [
      {
        kind: 'road',
        tile_width_m: 1.85,
        normal_strength: 0.34,
        maps: { diffuse: 'color', normal_gl: 'normal', roughness: 'rough' },
      },
    ];
    p.preview.meshes = [
      {
        id: 'road',
        kind: 'parking_apron',
        source: 'cad_surface',
        vertices: [
          [0, 0, 0],
          [3.7, 0, 0],
          [0, 3.7, 0],
        ],
        faces: [[0, 1, 2]],
      },
    ];
    const loader = nativeLoader();
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(p);
    await layers.whenReady();
    const mesh = layers.root.getObjectByName(
      'prepared-mesh:road',
    ) as THREE.Mesh;
    const material = mesh.material as THREE.MeshStandardMaterial;
    expect(material.map!.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(material.normalMap!.colorSpace).toBe(THREE.NoColorSpace);
    expect(material.roughnessMap!.colorSpace).toBe(THREE.NoColorSpace);
    expect(material.roughness).toBe(1);
    expect(material.normalScale.x).toBe(0.34);
    expect(mesh.geometry.getAttribute('uv').getX(1)).toBeCloseTo(2);
    const disposals = [
      material.map!,
      material.normalMap!,
      material.roughnessMap!,
    ].map((texture) => vi.spyOn(texture, 'dispose'));
    layers.dispose();
    layers.dispose();
    disposals.forEach((dispose) => expect(dispose).toHaveBeenCalledOnce());
  });

  it('shows shared facade panes and frames without adding reflective glass or duplicate geometry', () => {
    const p = preparation();
    p.preview.meshes = ['glass', 'frame', 'joint'].map((role, index) => ({
      id: role,
      kind: 'facade_detail',
      material_key: role,
      source: 'cad_building',
      vertices: [
        [index, 0, 0],
        [index + 0.5, 0, 0],
        [index, 0, 1],
      ],
      faces: [[0, 1, 2]],
    }));
    const original = structuredClone(p);
    const layers = new SceneEnvironmentLayers();
    layers.update(p);
    const glass = layers.root.getObjectByName(
      'prepared-mesh:glass',
    ) as THREE.Mesh;
    const frame = layers.root.getObjectByName(
      'prepared-mesh:frame',
    ) as THREE.Mesh;
    const material = glass.material as THREE.MeshStandardMaterial;
    expect(material.metalness).toBe(0);
    expect(material.roughness).toBe(1);
    expect(material.envMapIntensity).toBe(0);
    expect(material.transparent).toBe(false);
    expect(frame.castShadow).toBe(true);
    expect(p).toEqual(original);
    expect(layers.root.children[0].children).toHaveLength(3);
    layers.dispose();
  });
});
