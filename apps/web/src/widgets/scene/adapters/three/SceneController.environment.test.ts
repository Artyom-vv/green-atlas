import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ScenePlantObject } from '@green/api-client';
import { SceneController } from './SceneController';
import {
  SceneEnvironmentLayers,
  type ScenePreparedPreview,
} from './SceneEnvironmentLayers';
import type { PlantPrototype } from './plantAssets';
import type { PreparedPreviewAssetLoader } from './preparedPreviewAssets';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function prototype(id: string): PlantPrototype {
  const geometry = new THREE.BoxGeometry(2, 1, 2);
  geometry.translate(0, 0.5, 0);
  return {
    assetKey: id,
    lod: 'near',
    nominalHeightM: 1,
    nominalRadiusM: 1,
    parts: [
      {
        geometry,
        material: new THREE.MeshStandardMaterial(),
        billboard: false,
        castShadow: true,
      },
    ],
  };
}

function preparation(id = 'first'): ScenePreparedPreview {
  return {
    id,
    sha256: `${id}-sha`,
    project_id: 'project',
    scene_sha256: 'scene-sha',
    horizon_year: 0,
    geometry_version: 3,
    plan_version: 4,
    preview: {
      asset_version: 1,
      meshes: [],
      curbs: [],
      context_trees: [],
      suppressed_source_ids: [],
      assets: [
        {
          id: `${id}-model`,
          kind: 'model',
          lod: 'near',
          sha256: '0'.repeat(64),
          url: `/api/assets/${id}-model`,
        },
      ],
      planned_plants: [
        {
          object_id: 'planned',
          local_x: 13,
          local_y: -29,
          base_elevation_m: 151,
          height_m: 12,
          radius_m: 3,
          rotation_z: Math.PI / 2,
          model_asset_id: `${id}-model`,
        },
      ],
    },
  };
}

function controllerContext(layers: SceneEnvironmentLayers) {
  const p = preparation();
  return {
    disposed: false,
    scene: new THREE.Scene(),
    pendingEnvironmentLayers: undefined as SceneEnvironmentLayers | undefined,
    environmentGeneration: 0,
    environmentLayers: layers,
    environmentPreparation: undefined as ScenePreparedPreview | undefined,
    snapshot: {
      project_id: p.project_id,
      content_sha256: p.scene_sha256,
      horizon_year: p.horizon_year,
      geometry_version: p.geometry_version,
      plan_version: p.plan_version,
    },
    plants: { plantRoot: new THREE.Group(), rebuild: vi.fn() },
    selectedIds: new Set(['planned']),
    currentInstances: [],
    detachCsmMaterials: vi.fn(),
    rebuildContext: vi.fn(),
    rebuildPlants: vi.fn(),
    rebuildOutlines: vi.fn(),
    requestRender: vi.fn(),
    clearPreparedEnvironment:
      SceneController.prototype['clearPreparedEnvironment'],
  };
}

function update(
  context: ReturnType<typeof controllerContext>,
  p?: ScenePreparedPreview,
) {
  return SceneController.prototype.updateEnvironment.call(
    context as unknown as SceneController,
    p,
  );
}

describe('prepared environment controller lifecycle', () => {
  it('keeps the complete previous scene until a replacement loads, including after a failure', async () => {
    const replacement = deferred<PlantPrototype>();
    const loader: PreparedPreviewAssetLoader = {
      model: vi
        .fn()
        .mockResolvedValueOnce(prototype('first-native'))
        .mockImplementationOnce(() => replacement.promise),
      texture: vi.fn(),
    };
    const context = controllerContext(new SceneEnvironmentLayers(loader));
    expect(await update(context, preparation())).toBe(true);
    const displayed = context.environmentLayers;
    const dispose = vi.spyOn(displayed, 'dispose');
    const refreshing = update(context, preparation('replacement'));
    await Promise.resolve();
    expect(context.environmentLayers).toBe(displayed);
    expect(context.environmentPreparation?.id).toBe('first');
    expect(displayed.getPlannedPrototype('planned', 'near')?.assetKey).toBe(
      'first-native',
    );
    expect(dispose).not.toHaveBeenCalled();
    replacement.reject(new Error('Unavailable texture'));
    await expect(refreshing).rejects.toThrow('Unavailable texture');
    expect(context.environmentLayers).toBe(displayed);
    expect(context.environmentPreparation?.id).toBe('first');
    expect(dispose).not.toHaveBeenCalled();
    displayed.dispose();
  });

  it('discards a pending replacement when the source revision is cleared', async () => {
    const replacement = deferred<PlantPrototype>();
    const context = controllerContext(
      new SceneEnvironmentLayers({
        model: () => replacement.promise,
        texture: vi.fn(),
      }),
    );
    const pending = update(context, preparation());
    await update(context, undefined);
    const native = prototype('obsolete');
    const dispose = vi.spyOn(native.parts[0].geometry, 'dispose');
    replacement.resolve(native);
    expect(await pending).toBe(false);
    expect(context.environmentPreparation).toBeUndefined();
    expect(dispose).toHaveBeenCalledOnce();
  });
  it('confirms the displayed preparation only after its native models are loaded', async () => {
    const model = deferred<PlantPrototype>();
    const loader: PreparedPreviewAssetLoader = {
      model: vi.fn(() => model.promise),
      texture: vi.fn(),
    };
    const layers = new SceneEnvironmentLayers(loader),
      context = controllerContext(layers);
    let completed = false;
    const accepted = update(context, preparation()).then((value) => {
      completed = true;
      return value;
    });
    await Promise.resolve();
    expect(completed).toBe(false);
    expect(layers.getPlannedPrototype('planned', 'near')).toBeUndefined();
    const native = prototype('native');
    model.resolve(native);
    expect(await accepted).toBe(true);
    expect(
      context.environmentLayers.getPlannedPrototype('planned', 'near'),
    ).toBe(native);
    expect(context.rebuildPlants).toHaveBeenCalledOnce();
    layers.dispose();
  });

  it('does not let a late preparation replace the currently selected one', async () => {
    const first = deferred<PlantPrototype>();
    const loader: PreparedPreviewAssetLoader = {
      model: vi.fn((asset) =>
        asset.id === 'first-model'
          ? first.promise
          : Promise.resolve(prototype('second-native')),
      ),
      texture: vi.fn(),
    };
    const layers = new SceneEnvironmentLayers(loader),
      context = controllerContext(layers);
    const old = update(context, preparation('first'));
    expect(await update(context, preparation('second'))).toBe(true);
    const late = prototype('first-native');
    const dispose = vi.spyOn(late.parts[0].geometry, 'dispose');
    first.resolve(late);
    expect(await old).toBe(false);
    expect(context.environmentPreparation?.id).toBe('second');
    expect(
      context.environmentLayers.getPlannedPrototype('planned', 'near')
        ?.assetKey,
    ).toBe('second-native');
    expect(dispose).toHaveBeenCalledOnce();
    layers.dispose();
  });

  it('restores the source scene and editable plants after a failed native resource', async () => {
    const loader: PreparedPreviewAssetLoader = {
      model: vi.fn().mockRejectedValue(new Error('Missing GLB')),
      texture: vi.fn(),
    };
    const layers = new SceneEnvironmentLayers(loader),
      context = controllerContext(layers);
    await expect(update(context, preparation())).rejects.toThrow('Missing GLB');
    expect(context.environmentPreparation).toBeUndefined();
    expect(layers.root.children).toHaveLength(0);
    expect(context.rebuildContext).not.toHaveBeenCalled();
    expect(context.rebuildPlants).not.toHaveBeenCalled();
    expect(context.selectedIds.has('planned')).toBe(true);
    layers.dispose();
  });

  it('restores editable plants when preparation geometry fails before async loading starts', async () => {
    const layers = new SceneEnvironmentLayers(),
      context = controllerContext(layers);
    const p = preparation();
    p.preview.meshes = [
      {
        id: 'broken',
        kind: 'building',
        source: 'cad_building',
        vertices: [
          [0, 0, 0],
          [1, 0, 0],
          [0, 1, 0],
        ],
        faces: [[0, 1, 7]],
      },
    ];
    await expect(update(context, p)).rejects.toThrow('face');
    expect(context.rebuildPlants).not.toHaveBeenCalled();
    expect(context.environmentPreparation).toBeUndefined();
    expect(context.selectedIds.has('planned')).toBe(true);
    layers.dispose();
  });

  it('removes shadow references to an inactive LOD before its prepared material is disposed', () => {
    const inactive = new THREE.MeshStandardMaterial();
    inactive.defines = { USE_CSM: 1, CSM_CASCADES: 2, CSM_FADE: 1, OTHER: 7 };
    inactive.onBeforeCompile = vi.fn();
    const unrelated = new THREE.MeshStandardMaterial();
    const context = {
      csm: {
        shaders: new Map([
          [inactive, {}],
          [unrelated, {}],
        ]),
      },
      csmMaterials: new Set<THREE.Material>([inactive, unrelated]),
    };
    SceneController.prototype['detachCsmMaterials'].call(
      context as unknown as SceneController,
      new THREE.Group(),
      [inactive],
    );
    expect(context.csm.shaders.has(inactive)).toBe(false);
    expect(context.csmMaterials.has(inactive)).toBe(false);
    expect(Object.hasOwn(inactive, 'onBeforeCompile')).toBe(false);
    expect(inactive.defines).toEqual({ OTHER: 7 });
    expect(context.csm.shaders.has(unrelated)).toBe(true);
    inactive.dispose();
    unrelated.dispose();
  });

  it('removes editable wrappers before disposing their shared native prototypes', async () => {
    const native = prototype('native');
    const loader: PreparedPreviewAssetLoader = {
      model: vi.fn().mockResolvedValue(native),
      texture: vi.fn(),
    };
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(preparation());
    await layers.whenReady();
    const context = controllerContext(layers);
    context.environmentPreparation = preparation();
    const order: string[] = [];
    context.plants.rebuild.mockImplementation(() =>
      order.push('editable wrappers removed'),
    );
    vi.spyOn(native.parts[0].geometry, 'dispose').mockImplementation(() =>
      order.push('prototype disposed'),
    );
    SceneController.prototype['clearPreparedEnvironment'].call(
      context as unknown as SceneController,
    );
    expect(order).toEqual(['editable wrappers removed', 'prototype disposed']);
    expect(context.selectedIds).toEqual(new Set(['planned']));
    layers.dispose();
  });

  it('uses the prepared prototype and native transform while preserving the editable object ID', async () => {
    const native = prototype('native');
    const loader: PreparedPreviewAssetLoader = {
      model: vi.fn().mockResolvedValue(native),
      texture: vi.fn(),
    };
    const layers = new SceneEnvironmentLayers(loader);
    layers.update(preparation());
    await layers.whenReady();
    const object = {
      object_id: 'planned',
      kind: 'tree',
      local_x: 13,
      local_y: -29,
      canopy_radius_max_m: 3,
      height_max_m: 12,
    } as ScenePlantObject;
    const source = structuredClone(object);
    const context = {
      visibleObjects: () => [object],
      navigation: { projectedDiameter: () => 300 },
      previousLod: new Map(),
      selectedIds: new Set(['planned']),
      environmentLayers: layers,
      assetLibrary: { get: vi.fn() },
      terrainElevationAt: () => 0,
    };
    const instances = SceneController.prototype['collectInstances'].call(
      context as unknown as SceneController,
    );
    expect(instances).toHaveLength(1);
    expect(instances[0].object.object_id).toBe('planned');
    expect(instances[0].prototype).toBe(native);
    expect(context.assetLibrary.get).not.toHaveBeenCalled();
    const point = new THREE.Vector3(1, 0, 0).applyMatrix4(instances[0].matrix);
    expect(point.x).toBeCloseTo(13);
    expect(point.y).toBe(151);
    expect(point.z).toBeCloseTo(26);
    expect(object).toEqual(source);
    layers.dispose();
  });
});
