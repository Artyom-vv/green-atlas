import { describe, expect, it } from 'vitest';
import {
  buildPlantBatchPlan,
  classifySceneContentUpdate,
  classifySceneLod,
  evaluateDenseSceneTelemetry,
  evaluateSceneOutline,
  evaluateSceneResourceCycles,
  hasSceneHeapGrowth,
  evaluateSceneTelemetry,
  fallbackPlantAssetKey,
  nextSceneQualityState,
  sceneFrameWindow,
  SCENE_ACCESSIBILITY_CONTRACT,
  SCENE_QUALITY_BUDGET,
  SCENE_RENDER_TARGETS,
  SCENE_RENDERER_LIFECYCLE_KEY,
  SCENE_RESOURCE_TELEMETRY_ATTRIBUTES,
  SCENE_TELEMETRY_ATTRIBUTES,
  sceneDrawingBufferSize,
  sceneOutlineBufferWidth,
  type PlantBatchInput,
  type SceneLod,
} from '@/widgets/scene/model/sceneRenderContract';

const measuredRendererResources = {
  renderScale: 1 as const,
  effectivePixelRatio: 1.25,
  outlineWidthBufferPx: 2.5,
  geometries: 18,
  textures: 5,
  programs: 7,
  loadedPlantModels: 12,
};

describe('3D render contract', () => {
  it('distinguishes retained heap growth from post-GC measurement noise', () => {
    expect(
      hasSceneHeapGrowth([
        115_191_794, 123_442_007, 123_190_010, 111_837_925, 124_426_742,
      ]),
    ).toBe(false);
    expect(
      hasSceneHeapGrowth([
        103_119_427, 111_505_878, 122_219_049, 121_491_027, 121_915_835,
      ]),
    ).toBe(true);
    expect(
      hasSceneHeapGrowth([
        79_720_845, 67_333_653, 79_926_963, 78_845_238, 80_041_255,
      ]),
    ).toBe(false);
    expect(hasSceneHeapGrowth([100, undefined])).toBe(false);
  });
  it('keeps the renderer lifecycle independent from selection, roots and forecast state', () => {
    expect(SCENE_RENDERER_LIFECYCLE_KEY).toBe('green-atlas-webgl-scene-v1');
    const base = {
      planVersion: 7,
      horizonYear: 0,
      selectedIds: [] as string[],
      showRoots: false,
    };
    expect(
      classifySceneContentUpdate(base, { ...base, selectedIds: ['tree-1'] }),
    ).toBe('appearance');
    expect(classifySceneContentUpdate(base, { ...base, showRoots: true })).toBe(
      'appearance',
    );
    expect(classifySceneContentUpdate(base, { ...base, horizonYear: 20 })).toBe(
      'forecast',
    );
    expect(classifySceneContentUpdate(base, { ...base, planVersion: 8 })).toBe(
      'plan',
    );
    expect(
      classifySceneContentUpdate(
        { ...base, selectedIds: ['tree-1', 'tree-2'] },
        { ...base, selectedIds: ['tree-2', 'tree-1'] },
      ),
    ).toBe('none');
  });

  it('uses screen-space LOD with hysteresis instead of unstable distance thresholds', () => {
    expect(classifySceneLod(180)).toBe('near');
    expect(classifySceneLod(80)).toBe('mid');
    expect(classifySceneLod(12)).toBe('far');
    expect(classifySceneLod(154, 'near')).toBe('near');
    expect(classifySceneLod(159, 'mid')).toBe('mid');
    expect(classifySceneLod(36, 'far')).toBe('far');
    expect(classifySceneLod(170, 'mid')).toBe('near');
    expect(classifySceneLod(39, 'far')).toBe('mid');
  });

  it('batches 2500 varied plant models below the dense-scene draw-call budget', () => {
    const assetKeys = [
      'tilia-cordata',
      'acer-platanoides',
      'quercus-robur',
      'betula-pendula',
      'sorbus-aucuparia',
      'ulmus-laevis',
      'pinus-sylvestris',
      'picea-abies',
      'cornus-alba',
      'spiraea-japonica',
    ];
    const lods: SceneLod[] = ['near', 'mid', 'far'];
    const primitiveCounts: Record<SceneLod, number> = {
      near: 3,
      mid: 2,
      far: 1,
    };
    const objects: PlantBatchInput[] = Array.from(
      { length: SCENE_RENDER_TARGETS.densePlantCount },
      (_, index) => {
        const lod = lods[index % lods.length];
        return {
          objectId: `plant-${index}`,
          assetKey: assetKeys[index % assetKeys.length],
          lod,
          primitiveCount: primitiveCounts[lod],
        };
      },
    );

    const plan = buildPlantBatchPlan(objects);
    expect(plan.instanceCount).toBe(2_500);
    expect(plan.batches).toHaveLength(30);
    expect(plan.estimatedDrawCalls).toBe(60);
    expect(plan.estimatedDrawCalls).toBeLessThanOrEqual(
      SCENE_RENDER_TARGETS.denseMaxPlantDrawCalls,
    );
  });

  it('accounts for instance-buffer chunking and rejects invalid batch data', () => {
    const objects = Array.from({ length: 2_500 }, (_, index) => ({
      objectId: `tree-${index}`,
      assetKey: 'tilia-cordata',
      lod: 'near' as const,
      primitiveCount: 3,
    }));
    expect(buildPlantBatchPlan(objects).estimatedDrawCalls).toBe(9);
    expect(() => buildPlantBatchPlan([...objects, objects[0]])).toThrow(
      /Duplicate/,
    );
    expect(() =>
      buildPlantBatchPlan([{ ...objects[0], primitiveCount: 0 }]),
    ).toThrow(/primitive count/);
    expect(() => buildPlantBatchPlan(objects, 0)).toThrow(/positive integer/);
  });

  it('reports every failed dense-scene gate instead of hiding it in an average', () => {
    const healthy = {
      ...measuredRendererResources,
      rendererGeneration: 1,
      fps: 30,
      drawCalls: 120,
      triangles: 5_200_000,
      plantInstances: 2_500,
      visiblePlantInstances: 2_100,
    };
    expect(
      evaluateDenseSceneTelemetry(healthy, {
        initialRendererGeneration: 1,
        plantDrawCalls: 72,
      }),
    ).toEqual([]);
    expect(
      evaluateDenseSceneTelemetry(
        {
          ...healthy,
          rendererGeneration: 2,
          fps: 24,
          drawCalls: 220,
          triangles: 6_200_000,
          plantInstances: 2_499,
        },
        { initialRendererGeneration: 1, plantDrawCalls: 112 },
      ),
    ).toEqual([
      'renderer-recreated',
      'plant-instances-missing',
      'plant-draw-call-budget',
      'total-draw-call-budget',
      'triangle-budget',
      'dense-fps-floor',
    ]);
  });

  it('holds a typical scene to the 60 FPS target independently of the dense floor', () => {
    const telemetry = {
      ...measuredRendererResources,
      rendererGeneration: 1,
      fps: 59,
      drawCalls: 80,
      triangles: 640_000,
      plantInstances: 600,
      visiblePlantInstances: 520,
    };
    expect(
      evaluateSceneTelemetry(telemetry, {
        profile: 'typical',
        expectedPlantInstances: 600,
        plantDrawCalls: 40,
      }),
    ).toEqual(['target-fps']);
    expect(
      evaluateSceneTelemetry(
        { ...telemetry, fps: 60 },
        { profile: 'typical', expectedPlantInstances: 600, plantDrawCalls: 40 },
      ),
    ).toEqual([]);
  });

  it('enforces explicit 200–800 and 2500-plant profile boundaries', () => {
    const base = {
      ...measuredRendererResources,
      rendererGeneration: 1,
      fps: 60,
      drawCalls: 90,
      triangles: 2_000_000,
      visiblePlantInstances: 200,
    };
    expect(
      evaluateSceneTelemetry(
        { ...base, plantInstances: 200 },
        { profile: 'typical', expectedPlantInstances: 200, plantDrawCalls: 50 },
      ),
    ).toEqual([]);
    expect(
      evaluateSceneTelemetry(
        { ...base, plantInstances: 800, visiblePlantInstances: 800 },
        { profile: 'typical', expectedPlantInstances: 800, plantDrawCalls: 64 },
      ),
    ).toEqual([]);
    expect(
      evaluateSceneTelemetry(
        { ...base, plantInstances: 801, visiblePlantInstances: 801 },
        { profile: 'typical', expectedPlantInstances: 801, plantDrawCalls: 64 },
      ),
    ).toEqual(['plant-count-budget']);
    expect(
      evaluateDenseSceneTelemetry(
        {
          ...base,
          fps: 30,
          drawCalls: 180,
          triangles: 6_000_000,
          plantInstances: 2_500,
          visiblePlantInstances: 2_500,
        },
        { plantDrawCalls: 96 },
      ),
    ).toEqual([]);
  });

  it('rejects impossible instance counters and zero-FPS warm-up as measurements', () => {
    expect(
      evaluateSceneTelemetry(
        {
          ...measuredRendererResources,
          rendererGeneration: 1,
          fps: 0,
          drawCalls: 20,
          triangles: 200_000,
          plantInstances: 400,
          visiblePlantInstances: 401,
        },
        { profile: 'typical', plantDrawCalls: 10 },
      ),
    ).toEqual(['visible-instance-overcount', 'telemetry-warming-up']);
  });

  it('degrades and recovers render scale only after sustained samples and cooldown', () => {
    const initial = { renderScale: 1.25 as const, lastChangedAt: 0 };
    expect(
      nextSceneQualityState(initial, {
        p90FrameMs: 22,
        durationMs: 999,
        measuredAt: 1_000,
      }),
    ).toBe(initial);
    const degraded = nextSceneQualityState(initial, {
      p90FrameMs: 22,
      durationMs: 1_000,
      measuredAt: 1_000,
    });
    expect(degraded).toEqual({ renderScale: 1, lastChangedAt: 1_000 });
    expect(
      nextSceneQualityState(degraded, {
        p90FrameMs: 24,
        durationMs: 1_000,
        measuredAt: 1_500,
      }),
    ).toBe(degraded);
    const degradedAgain = nextSceneQualityState(degraded, {
      p90FrameMs: 24,
      durationMs: 1_000,
      measuredAt: 2_000,
    });
    expect(degradedAgain.renderScale).toBe(0.85);
    expect(
      nextSceneQualityState(degradedAgain, {
        p90FrameMs: 13,
        durationMs: 2_999,
        measuredAt: 5_000,
      }),
    ).toBe(degradedAgain);
    expect(
      nextSceneQualityState(degradedAgain, {
        p90FrameMs: 13,
        durationMs: 3_000,
        measuredAt: 5_000,
      }),
    ).toEqual({
      renderScale: 1,
      lastChangedAt: 5_000,
    });
  });

  it('derives p90 frame time from raw animation-frame samples', () => {
    const frames = [...Array.from({ length: 9 }, () => 16), 31];
    expect(sceneFrameWindow(frames, 1_000, 4_200)).toEqual({
      p90FrameMs: 16,
      durationMs: 1_000,
      measuredAt: 4_200,
    });
    expect(sceneFrameWindow([...frames, 34], 1_100, 4_300).p90FrameMs).toBe(31);
    expect(() => sceneFrameWindow([], 0, 0)).toThrow(/Frame window/);
  });

  it('caps drawing-buffer pixels while retaining a truthful effective DPR', () => {
    const capped = sceneDrawingBufferSize(1_920, 1_080, 2, 1.25);
    expect(capped.width * capped.height).toBeLessThanOrEqual(
      SCENE_QUALITY_BUDGET.maxDrawingBufferPixels,
    );
    expect(capped).toEqual(
      expect.objectContaining({
        width: 1_920,
        height: 1_080,
        effectivePixelRatio: 1,
      }),
    );
    expect(sceneDrawingBufferSize(1_000, 500, 2, 0.7)).toEqual({
      width: 1_049,
      height: 524,
      effectivePixelRatio: 1.0499999999999998,
      renderScale: 0.7,
    });
  });

  it('keeps outline width stable in screen space across camera angles and DPR', () => {
    const samples = [
      {
        azimuthDeg: 0,
        polarDeg: 25,
        effectivePixelRatio: 1,
        outlineWidthBufferPx: sceneOutlineBufferWidth(1),
      },
      {
        azimuthDeg: 90,
        polarDeg: 55,
        effectivePixelRatio: 1.25,
        outlineWidthBufferPx: sceneOutlineBufferWidth(1.25),
      },
      {
        azimuthDeg: 215,
        polarDeg: 78,
        effectivePixelRatio: 1.5,
        outlineWidthBufferPx: sceneOutlineBufferWidth(1.5),
      },
    ];
    expect(evaluateSceneOutline(samples)).toEqual([]);
    expect(
      evaluateSceneOutline([
        samples[0],
        { ...samples[1], outlineWidthBufferPx: 3.25 },
      ]),
    ).toEqual(['outline-width', 'outline-angle-drift']);
  });

  it('detects retained GPU and heap resources after repeated 2D↔3D cycles', () => {
    const baseline = {
      activeRenderers: 0,
      createdRenderers: 0,
      disposedRenderers: 0,
      geometries: 0,
      textures: 0,
      programs: 0,
      jsHeapBytes: 100_000_000,
    };
    expect(
      evaluateSceneResourceCycles(baseline, {
        ...baseline,
        createdRenderers: 20,
        disposedRenderers: 20,
        jsHeapBytes: 104_000_000,
      }),
    ).toEqual([]);
    expect(
      evaluateSceneResourceCycles(baseline, {
        activeRenderers: 2,
        createdRenderers: 20,
        disposedRenderers: 18,
        geometries: 2,
        textures: 3,
        programs: 2,
        jsHeapBytes: 106_000_000,
      }),
    ).toEqual([
      'renderer-overlap',
      'renderer-leak',
      'geometry-growth',
      'texture-growth',
      'program-growth',
      'heap-growth',
    ]);
  });

  it('publishes an accessibility and live telemetry surface for browser verification', () => {
    expect(SCENE_ACCESSIBILITY_CONTRACT).toEqual(
      expect.objectContaining({
        canvasLabel: '3D-сцена посадок',
        focusSelectionLabel: 'Приблизить выбранную посадку',
        resetCameraLabel: 'Показать всю территорию',
        outlineWidthPx: 6,
      }),
    );
    expect(new Set(Object.values(SCENE_TELEMETRY_ATTRIBUTES)).size).toBe(14);
    expect(
      Object.values(SCENE_TELEMETRY_ATTRIBUTES).every((attribute) =>
        attribute.startsWith('data-scene-'),
      ),
    ).toBe(true);
    expect(
      new Set(Object.values(SCENE_RESOURCE_TELEMETRY_ATTRIBUTES)).size,
    ).toBe(7);
  });

  it('uses exact species revisions before a documented morphological fallback', () => {
    expect(
      fallbackPlantAssetKey({
        kind: 'tree',
        species_revision_id: 'tilia-cordata@2026-08-28.1',
        crown_shape: 'round',
      }),
    ).toBe('tilia-cordata@2026-08-28.1');
    expect(
      fallbackPlantAssetKey({
        kind: 'shrub',
        species_revision_id: null,
        crown_shape: 'spreading',
      }),
    ).toBe('shrub:spreading');
  });
});
