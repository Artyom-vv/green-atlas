import { afterEach, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { SceneSnapshot } from '@green/api-client';
import type { PlanViewState } from '@/entities/editor/model/planViewState';
import { SceneCamera } from './SceneCamera';
import { SceneController } from './SceneController';

afterEach(() => vi.restoreAllMocks());

function snapshot(year = 0): SceneSnapshot {
  return {
    project_id: 'forecast-camera',
    plan_version: 4,
    geometry_version: 2,
    horizon_year: year,
    coordinate_origin: [1000, 2000],
    georeference_status: 'local',
    geometry_source: 'prepared_geometry',
    completeness: 'partial',
    terrain_status: 'missing',
    building_heights_status: 'missing',
    building_feature_count: 0,
    building_height_confirmed_count: 0,
    note: 'Прогноз роста',
    data_gaps: [],
    objects: [0, 100].map((x, index) => ({
      object_id: `tree-${index}`,
      kind: 'tree',
      size_class: 'standard',
      growth_stage: year ? 'developing' : 'planting',
      growth_stage_status: 'estimated',
      forecast_horizon_year: year,
      local_x: x,
      local_y: 0,
      crown_shape: 'round',
      canopy_radius_min_m: 1,
      canopy_radius_max_m: year ? 4 : 1,
      height_max_m: year ? 10 : 3,
      height_status: 'estimated',
      confidence: 'unknown',
      status: 'valid',
      locked: false,
    })),
  };
}

/** Real camera and controller commands, without unrelated GPU construction. */
function setup(initialViewState?: PlanViewState) {
  vi.spyOn(performance, 'now').mockReturnValue(100);
  const canvas = document.createElement('canvas');
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(
    new DOMRect(0, 0, 900, 600),
  );
  const navigation = new SceneCamera(canvas, {
    initialViewState,
    onMove: vi.fn(),
    onClipChange: vi.fn(),
  });
  const context = {
    navigation,
    contextRoot: new THREE.Group(),
    objectById: new Map(),
    issueSeverity: new Map(),
    selectedIds: new Set(['tree-0']),
    rebuildContext: vi.fn(() => navigation.setBasis([1000, 2000], () => 0)),
    rebuildPlants: vi.fn(),
    requestRender: vi.fn(),
  };
  const controller = Object.assign(
    Object.create(SceneController.prototype),
    context,
  ) as SceneController;
  return {
    controller,
    navigation,
    context,
    settle: () => {
      navigation.advance(2000);
      navigation.controls.update();
    },
    pose: () => {
      const camera = navigation.exportRenderCamera();
      // MapControls can add floating-point roundoff on a stationary update.
      const stable = (values: number[]) =>
        values.map((value) => Number(value.toFixed(9)));
      return {
        camera: camera && {
          ...camera,
          position: stable(camera.position),
          quaternion: stable(camera.quaternion),
        },
        target: stable(navigation.controls.target.toArray()),
        mode: navigation.mode,
      };
    },
  };
}

it('keeps the selected planting view when growth changes the displayed sizes', () => {
  const test = setup();
  try {
    test.controller.updateSnapshot(snapshot());
    test.controller.fitSelection();
    test.settle();
    expect(test.navigation.controls.target.x).toBeCloseTo(0);
    const before = test.pose();

    test.controller.updateSnapshot(snapshot(1));
    test.settle();
    expect(test.pose()).toEqual(before);
    expect(test.navigation.animating).toBe(false);
    expect(test.context.rebuildPlants).toHaveBeenCalledTimes(2);

    // The explicit action still fits the selected plant's updated dimensions.
    test.controller.fitSelection();
    test.settle();
    expect(test.pose()).not.toEqual(before);
    expect(test.navigation.controls.target.x).toBeCloseTo(0);
  } finally {
    test.navigation.dispose();
  }
});

it('keeps an explicitly placed street camera through forecast and return to now', () => {
  const test = setup();
  try {
    test.controller.updateSnapshot(snapshot());
    expect(
      test.navigation.placeStreetCamera([1010, 2020], [1030, 2020]),
    ).toBe(true);
    const before = test.pose();
    for (const year of [1, 9, 0]) {
      test.controller.updateSnapshot(snapshot(year));
      test.settle();
      expect(test.pose()).toEqual(before);
      expect(test.navigation.animating).toBe(false);
    }

    // A user command can still replace the street view with the overview.
    test.controller.setCameraMode('overview');
    test.settle();
    expect(test.navigation.mode).toBe('overview');
    expect(test.pose()).not.toEqual(before);
  } finally {
    test.navigation.dispose();
  }
});

it('still fits the first populated snapshot after an empty initial scene', () => {
  const test = setup();
  const fit = vi.spyOn(test.navigation, 'fitBounds');
  try {
    test.controller.updateSnapshot({ ...snapshot(), objects: [] });
    expect(fit).toHaveBeenCalledTimes(1);
    const emptyPose = test.pose();
    test.controller.updateSnapshot(snapshot());
    expect(fit).toHaveBeenCalledTimes(2);
    expect(fit.mock.calls[1][1]).toBe(false);
    expect(test.navigation.controls.target.x).toBeCloseTo(50);
    expect(test.pose()).not.toEqual(emptyPose);
  } finally {
    test.navigation.dispose();
  }
});

it('keeps the incoming 2D viewport authoritative over the initial planting fit', () => {
  const view: PlanViewState = {
    center: [1010, 2020],
    resolution: 0.5,
    rotation: 0.7,
    viewport: [900, 600],
  };
  const test = setup(view);
  const fit = vi.spyOn(test.navigation, 'fitBounds');
  try {
    test.controller.updateSnapshot(snapshot());
    expect(fit).not.toHaveBeenCalled();
    expect(test.navigation.hasPendingView).toBe(false);
    const transferred = test.navigation.exportPlanViewState();
    expect(transferred?.center[0]).toBeCloseTo(view.center[0], 6);
    expect(transferred?.center[1]).toBeCloseTo(view.center[1], 6);
    expect(transferred?.resolution).toBeCloseTo(view.resolution, 6);
    expect(transferred?.rotation).toBeCloseTo(view.rotation, 6);
  } finally {
    test.navigation.dispose();
  }
});
