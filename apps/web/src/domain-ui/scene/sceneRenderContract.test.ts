import { describe, expect, it } from 'vitest';
import {
  buildPlantBatchPlan,
  classifySceneContentUpdate,
  classifySceneLod,
  evaluateDenseSceneTelemetry,
  evaluateSceneTelemetry,
  fallbackPlantAssetKey,
  SCENE_ACCESSIBILITY_CONTRACT,
  SCENE_RENDER_TARGETS,
  SCENE_RENDERER_LIFECYCLE_KEY,
  SCENE_TELEMETRY_ATTRIBUTES,
  type PlantBatchInput,
  type SceneLod,
} from './sceneRenderContract';

describe('3D render contract', () => {
  it('keeps the renderer lifecycle independent from selection, roots and forecast state', () => {
    expect(SCENE_RENDERER_LIFECYCLE_KEY).toBe('green-atlas-webgl-scene-v1');
    const base = { planVersion: 7, horizonYear: 0, selectedIds: [] as string[], showRoots: false };
    expect(classifySceneContentUpdate(base, { ...base, selectedIds: ['tree-1'] })).toBe('appearance');
    expect(classifySceneContentUpdate(base, { ...base, showRoots: true })).toBe('appearance');
    expect(classifySceneContentUpdate(base, { ...base, horizonYear: 20 })).toBe('forecast');
    expect(classifySceneContentUpdate(base, { ...base, planVersion: 8 })).toBe('plan');
    expect(classifySceneContentUpdate(
      { ...base, selectedIds: ['tree-1', 'tree-2'] },
      { ...base, selectedIds: ['tree-2', 'tree-1'] },
    )).toBe('none');
  });

  it('uses screen-space LOD with hysteresis instead of unstable distance thresholds', () => {
    expect(classifySceneLod(120)).toBe('near');
    expect(classifySceneLod(60)).toBe('mid');
    expect(classifySceneLod(12)).toBe('far');
    expect(classifySceneLod(92, 'near')).toBe('near');
    expect(classifySceneLod(99, 'mid')).toBe('mid');
    expect(classifySceneLod(28, 'far')).toBe('far');
    expect(classifySceneLod(103, 'mid')).toBe('near');
    expect(classifySceneLod(31, 'far')).toBe('mid');
  });

  it('batches 2100 varied plant models below the dense-scene draw-call budget', () => {
    const assetKeys = [
      'tilia-cordata', 'acer-platanoides', 'quercus-robur', 'betula-pendula',
      'sorbus-aucuparia', 'ulmus-laevis', 'pinus-sylvestris', 'picea-abies',
      'cornus-alba', 'spiraea-japonica',
    ];
    const lods: SceneLod[] = ['near', 'mid', 'far'];
    const primitiveCounts: Record<SceneLod, number> = { near: 3, mid: 2, far: 1 };
    const objects: PlantBatchInput[] = Array.from({ length: SCENE_RENDER_TARGETS.densePlantCount }, (_, index) => {
      const lod = lods[index % lods.length];
      return {
        objectId: `plant-${index}`,
        assetKey: assetKeys[index % assetKeys.length],
        lod,
        primitiveCount: primitiveCounts[lod],
      };
    });

    const plan = buildPlantBatchPlan(objects);
    expect(plan.instanceCount).toBe(2_100);
    expect(plan.batches).toHaveLength(30);
    expect(plan.estimatedDrawCalls).toBe(60);
    expect(plan.estimatedDrawCalls).toBeLessThanOrEqual(SCENE_RENDER_TARGETS.denseMaxPlantDrawCalls);
  });

  it('accounts for instance-buffer chunking and rejects invalid batch data', () => {
    const objects = Array.from({ length: 2_100 }, (_, index) => ({
      objectId: `tree-${index}`,
      assetKey: 'tilia-cordata',
      lod: 'near' as const,
      primitiveCount: 3,
    }));
    expect(buildPlantBatchPlan(objects).estimatedDrawCalls).toBe(9);
    expect(() => buildPlantBatchPlan([...objects, objects[0]])).toThrow(/Duplicate/);
    expect(() => buildPlantBatchPlan([{ ...objects[0], primitiveCount: 0 }])).toThrow(/primitive count/);
    expect(() => buildPlantBatchPlan(objects, 0)).toThrow(/positive integer/);
  });

  it('reports every failed dense-scene gate instead of hiding it in an average', () => {
    const healthy = {
      rendererGeneration: 1,
      fps: 58,
      drawCalls: 120,
      triangles: 1_200_000,
      plantInstances: 2_100,
      visiblePlantInstances: 1_700,
    };
    expect(evaluateDenseSceneTelemetry(healthy, { initialRendererGeneration: 1, plantDrawCalls: 72 })).toEqual([]);
    expect(evaluateDenseSceneTelemetry(
      { ...healthy, rendererGeneration: 2, fps: 24, drawCalls: 220, plantInstances: 2_099 },
      { initialRendererGeneration: 1, plantDrawCalls: 112 },
    )).toEqual([
      'renderer-recreated',
      'plant-instances-missing',
      'plant-draw-call-budget',
      'total-draw-call-budget',
      'dense-fps-floor',
    ]);
  });

  it('holds a typical scene to the 60 FPS target independently of the dense floor', () => {
    const telemetry = {
      rendererGeneration: 1,
      fps: 59,
      drawCalls: 80,
      triangles: 640_000,
      plantInstances: 600,
      visiblePlantInstances: 520,
    };
    expect(evaluateSceneTelemetry(telemetry, { profile: 'typical', expectedPlantInstances: 600 })).toEqual(['target-fps']);
    expect(evaluateSceneTelemetry({ ...telemetry, fps: 60 }, { profile: 'typical', expectedPlantInstances: 600 })).toEqual([]);
  });

  it('publishes an accessibility and live telemetry surface for browser verification', () => {
    expect(SCENE_ACCESSIBILITY_CONTRACT).toEqual(expect.objectContaining({
      canvasLabel: '3D-сцена посадок',
      focusSelectionLabel: 'Приблизить выбранную посадку',
      resetCameraLabel: 'Показать всю территорию',
      outlineWidthPx: 2,
    }));
    expect(new Set(Object.values(SCENE_TELEMETRY_ATTRIBUTES)).size).toBe(6);
    expect(Object.values(SCENE_TELEMETRY_ATTRIBUTES).every((attribute) => attribute.startsWith('data-scene-'))).toBe(true);
  });

  it('uses exact species revisions before a documented morphological fallback', () => {
    expect(fallbackPlantAssetKey({ kind: 'tree', species_revision_id: 'tilia-cordata@2026-08-28.1', crown_shape: 'round' })).toBe('tilia-cordata@2026-08-28.1');
    expect(fallbackPlantAssetKey({ kind: 'shrub', species_revision_id: null, crown_shape: 'spreading' })).toBe('shrub:spreading');
  });
});
