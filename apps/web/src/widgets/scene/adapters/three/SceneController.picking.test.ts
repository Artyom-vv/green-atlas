import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneController } from './SceneController';
import {
  ScenePlantLayers,
  pickingColorForIndex,
  pickingIndexFromPixel,
} from './ScenePlantLayers';
import * as THREE from 'three';
import { SceneCamera } from './SceneCamera';
import type { ScenePlantObject } from '@green/api-client';
afterEach(() => vi.restoreAllMocks());

it('exports the actual tilted camera pose and visible viewport for Blender', () => {
  const canvas = document.createElement('canvas');
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(
    new DOMRect(0, 0, 900, 600),
  );
  const navigation = new SceneCamera(canvas, {
    onMove: vi.fn(),
    onClipChange: vi.fn(),
  });
  try {
    expect(navigation.exportRenderCamera()).toBeUndefined();
    navigation.setBasis([1000, 2000], () => 0);
    navigation.camera.position.set(23, 17, -41);
    navigation.camera.lookAt(new THREE.Vector3(-3, 0, 5));
    const pose = navigation.exportRenderCamera()!;
    expect(pose.position).toEqual([23, 17, -41]);
    expect(pose.quaternion).toEqual(navigation.camera.quaternion.toArray());
    expect(pose.aspect).toBe(1.5);
    expect(pose.fov_y_deg).toBe(42);
  } finally {
    navigation.dispose();
  }
});

describe('GPU plant picking IDs', () => {
  it('round-trips IDs across all RGB bytes and reserves black for background', () => {
    for (const index of [0, 1, 254, 255, 256, 65_534, 65_535, 1_000_000]) {
      const color = pickingColorForIndex(index).map((channel) =>
        Math.round(channel * 255),
      );
      expect(pickingIndexFromPixel(color)).toBe(index);
    }
    expect(pickingIndexFromPixel([0, 0, 0, 0])).toBeUndefined();
  });
});

it('fits an empty work area in source coordinates at its real terrain elevation', () => {
  const camera = new SceneCamera(document.createElement('canvas'), {
    onMove: vi.fn(),
    onClipChange: vi.fn(),
  });
  camera.setBasis([1000, 2000], () => 150);
  const fit = vi.spyOn(camera, 'fitBounds').mockImplementation(() => {});
  try {
    camera.fitExtent([1010, 2020, 1060, 2080]);
    const bounds = fit.mock.calls[0][0];
    expect(bounds.min.toArray()).toEqual([10, 150, -80]);
    expect(bounds.max.toArray()).toEqual([60, 150, -20]);
  } finally {
    camera.dispose();
  }
});

it('does not silently frame only the densest subgroup when asked to show plantings', () => {
  const all = [{ object_id: 'a' }, { object_id: 'b' }];
  const context = {
    snapshot: { objects: all },
    navigation: {
      mode: 'custom',
      boundsForObjects: vi.fn(() => 'bounds'),
      fitBounds: vi.fn(),
    },
  };
  SceneController.prototype.fitPlantings.call(
    context as unknown as SceneController,
  );
  expect(context.navigation.boundsForObjects).toHaveBeenCalledWith(all);
  expect(context.navigation.fitBounds).toHaveBeenCalledWith('bounds', true);
});

it('frames every selected object rather than only the first tree of a group', () => {
  const one = { object_id: 'one' },
    two = { object_id: 'two' },
    bounds = {};
  const context = {
    selectedIds: new Set(['one', 'two', 'missing']),
    objectById: new Map([
      ['one', one],
      ['two', two],
    ]),
    navigation: {
      mode: 'ground',
      boundsForObjects: vi.fn(() => bounds),
      fitBounds: vi.fn(),
    },
    fitAll: vi.fn(),
  };
  SceneController.prototype.fitSelection.call(
    context as unknown as SceneController,
  );
  expect(context.navigation.boundsForObjects).toHaveBeenCalledWith([one, two]);
  expect(context.navigation.fitBounds).toHaveBeenCalledWith(bounds, true);
  expect(context.navigation.mode).toBe('overview');
});

it('retains an incoming view until source basis exists, then transfers its measured scale and centre', () => {
  const canvas = document.createElement('canvas');
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(
    new DOMRect(0, 0, 800, 600),
  );
  const view = {
    center: [1010, 2020] as [number, number],
    resolution: 0.5,
    rotation: 0.7,
    viewport: [800, 600] as [number, number],
  };
  const changed = vi.fn();
  const camera = new SceneCamera(canvas, {
    initialViewState: view,
    onMove: vi.fn(),
    onClipChange: vi.fn(),
    onViewStateChange: changed,
  });
  try {
    expect(camera.applyPendingPlanViewState()).toBe(false);
    expect(camera.hasPendingView).toBe(true);
    camera.setBasis([1000, 2000], () => 150);
    expect(camera.applyPendingPlanViewState()).toBe(true);
    const exported = camera.exportPlanViewState();
    expect(exported?.center[0]).toBeCloseTo(1010, 6);
    expect(exported?.center[1]).toBeCloseTo(2020, 6);
    expect(exported?.resolution).toBeCloseTo(0.5, 6);
    expect(exported?.rotation).toBeCloseTo(0.7, 6);
    expect(camera.hasPendingView).toBe(false);
    expect(changed).toHaveBeenCalledWith(exported);
  } finally {
    camera.dispose();
  }
});

it('completes a camera animation once and constrains panning without changing camera-target separation', () => {
  vi.spyOn(performance, 'now').mockReturnValue(100);
  const moved = vi.fn();
  const camera = new SceneCamera(document.createElement('canvas'), {
    onMove: moved,
    onClipChange: vi.fn(),
  });
  try {
    camera.setBasis([0, 0], () => 0);
    camera.fitBounds(
      new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(20, 5, 10)),
      true,
    );
    expect(moved).toHaveBeenLastCalledWith(680);
    expect(camera.advance(440)).toBe(false);
    expect(camera.animating).toBe(true);
    expect(camera.advance(780)).toBe(true);
    expect(camera.advance(900)).toBe(false);
    expect(camera.animating).toBe(false);
    expect(camera.controls.target.x).toBeCloseTo(10);
    expect(camera.controls.target.z).toBeCloseTo(5);
    camera.bounds.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(20, 5, 10));
    camera.controls.target.set(1000, 0, -1000);
    const separation = camera.camera.position
      .clone()
      .sub(camera.controls.target);
    camera.constrainNavigationToSite();
    expect(camera.controls.target.x).toBe(28);
    expect(camera.controls.target.z).toBe(-8);
    expect(
      camera.camera.position
        .clone()
        .sub(camera.controls.target)
        .distanceTo(separation),
    ).toBeLessThan(1e-10);
  } finally {
    camera.dispose();
  }
});

it('rebuilds plant/pick/outline instances without destroying shared assets and restores GPU picking state', () => {
  const canvas = document.createElement('canvas');
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(
    new DOMRect(0, 0, 800, 600),
  );
  const camera = new THREE.PerspectiveCamera();
  const previousTarget = new THREE.WebGLRenderTarget(2, 2);
  const renderer = {
    getRenderTarget: vi.fn(() => previousTarget),
    getClearColor: vi.fn((target: THREE.Color) => target.set(0x123456)),
    getClearAlpha: vi.fn(() => 0.5),
    setRenderTarget: vi.fn(),
    setClearColor: vi.fn(),
    clear: vi.fn(),
    render: vi.fn(),
    readRenderTargetPixels: vi.fn(
      (_target, _x, _y, _width, _height, pixels: Uint8Array) => {
        pixels.set([
          ...pickingColorForIndex(0).map((channel) =>
            Math.round(channel * 255),
          ),
          255,
        ]);
      },
    ),
  };
  const layers = new ScenePlantLayers(
    canvas,
    camera,
    renderer,
    new THREE.Scene(),
  );
  const geometry = new THREE.BoxGeometry();
  const material = new THREE.MeshBasicMaterial();
  const disposeGeometry = vi.spyOn(geometry, 'dispose');
  const disposeMaterial = vi.spyOn(material, 'dispose');
  const object: ScenePlantObject = {
    object_id: 'one',
    kind: 'tree',
    size_class: 'standard',
    growth_stage: 'planting',
    growth_stage_status: 'estimated',
    forecast_horizon_year: 0,
    local_x: 0,
    local_y: 0,
    crown_shape: 'round',
    canopy_radius_min_m: 1,
    canopy_radius_max_m: 2,
    root_radius_min_m: 1.7,
    root_radius_max_m: 6.85,
    height_status: 'estimated',
    confidence: 'unknown',
    status: 'valid',
    locked: false,
  };
  const prototype = {
    assetKey: 'tree',
    lod: 'near' as const,
    nominalHeightM: 3,
    nominalRadiusM: 1,
    parts: [{ geometry, material, castShadow: true, billboard: false }],
  };
  const instance = {
    object,
    prototype,
    lod: 'near' as const,
    matrix: new THREE.Matrix4(),
  };
  try {
    layers.attach();
    layers.rebuild([instance]);
    const previousMesh = layers.plantRoot.children[0];
    const previousPick = layers.pickRoot.children[0];
    layers.rebuildOutlines([instance], new Set(['one']), undefined, new Map());
    expect(layers.outlinePass.selectedObjects).toEqual([
      layers.outlineRoots.selected,
    ]);
    expect(layers.objectAt({ clientX: 40, clientY: 60 })).toBe('one');
    expect(renderer.setRenderTarget).toHaveBeenLastCalledWith(previousTarget);
    expect(renderer.setClearColor).toHaveBeenLastCalledWith(
      expect.any(THREE.Color),
      0.5,
    );
    expect(camera.view?.enabled).toBe(false);
    layers.rebuildRoots([instance], true, () => 0);
    const rootMesh = layers.rootsRoot.children[0] as THREE.InstancedMesh;
    const rootMatrix = new THREE.Matrix4();
    rootMesh.getMatrixAt(0, rootMatrix);
    const rootScale = new THREE.Vector3();
    rootMatrix.decompose(
      new THREE.Vector3(),
      new THREE.Quaternion(),
      rootScale,
    );
    expect(rootScale.x).toBeCloseTo(1.7);
    expect(object.root_radius_max_m).toBe(6.85);
    layers.rebuildRoots(
      [{ ...instance, object: { ...object, root_radius_min_m: null } }],
      true,
      () => 0,
    );
    expect(layers.rootsRoot.children).toHaveLength(0);
    layers.rebuild([{ ...instance, object: { ...object, object_id: 'two' } }]);
    expect(previousMesh.parent).toBeNull();
    expect(previousPick.parent).toBeNull();
    expect(layers.pickIds).toEqual(['two']);
    layers.clearInstances();
    expect(layers.plantRoot.children).toHaveLength(0);
    expect(layers.pickRoot.children).toHaveLength(0);
    expect(layers.outlineRoots.selected.children).toHaveLength(0);
    expect(disposeGeometry).not.toHaveBeenCalled();
    expect(disposeMaterial).not.toHaveBeenCalled();
  } finally {
    layers.clearInstances();
    layers.dispose();
    previousTarget.dispose();
    geometry.dispose();
    material.dispose();
  }
});
