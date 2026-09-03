import type { ScenePlantObject } from '@green/api-client';

/**
 * Measurable acceptance limits for the browser scene. These values are kept
 * outside the renderer so unit tests, browser diagnostics and the runtime all
 * use the same definition of "smooth enough".
 */
export const SCENE_RENDER_TARGETS = Object.freeze({
  targetFps: 60,
  denseMinimumFps: 30,
  densePlantCount: 2_100,
  /** Plant models only. Context, shadows and post-processing have a separate budget. */
  denseMaxPlantDrawCalls: 96,
  denseMaxTotalDrawCalls: 180,
  maxDevicePixelRatio: 1.5,
  maxInstancesPerBatch: 1_024,
});

export const SCENE_LOD_THRESHOLDS_PX = Object.freeze({
  /** Above this on-screen diameter the detailed model is useful. */
  near: 96,
  /** Below this diameter a silhouette/billboard is visually sufficient. */
  mid: 24,
  /** Prevents a model from oscillating between LODs around a threshold. */
  hysteresis: 6,
});

export type SceneLod = 'near' | 'mid' | 'far';

/**
 * LOD is based on projected size, not world distance. The same tree therefore
 * keeps the right amount of detail across camera FOVs and viewport sizes.
 */
export function classifySceneLod(projectedDiameterPx: number, previous?: SceneLod): SceneLod {
  const diameter = Number.isFinite(projectedDiameterPx) ? Math.max(0, projectedDiameterPx) : 0;
  const { near, mid, hysteresis } = SCENE_LOD_THRESHOLDS_PX;

  if (previous === 'near' && diameter >= near - hysteresis) return 'near';
  if (previous === 'mid') {
    if (diameter >= near + hysteresis) return 'near';
    if (diameter >= mid - hysteresis) return 'mid';
  }
  if (previous === 'far' && diameter < mid + hysteresis) return 'far';
  if (diameter >= near) return 'near';
  if (diameter >= mid) return 'mid';
  return 'far';
}

/**
 * A renderer belongs to the mounted canvas, not to mutable scene content.
 * Selection, roots and forecast horizon intentionally do not participate in
 * this key; changing them must update buffers/uniforms without losing WebGL.
 */
export const SCENE_RENDERER_LIFECYCLE_KEY = 'green-atlas-webgl-scene-v1' as const;

export type SceneContentState = {
  planVersion: number;
  horizonYear: number;
  selectedIds: readonly string[];
  showRoots: boolean;
};

export type SceneContentUpdate = 'none' | 'appearance' | 'forecast' | 'plan';

/** Returns the most expensive update required while keeping the renderer alive. */
export function classifySceneContentUpdate(previous: SceneContentState, next: SceneContentState): SceneContentUpdate {
  if (previous.planVersion !== next.planVersion) return 'plan';
  if (previous.horizonYear !== next.horizonYear) return 'forecast';
  if (previous.showRoots !== next.showRoots || !sameIdSet(previous.selectedIds, next.selectedIds)) return 'appearance';
  return 'none';
}

function sameIdSet(left: readonly string[], right: readonly string[]) {
  if (left.length !== right.length) return false;
  const values = new Set(left);
  return right.every((value) => values.has(value));
}

export type PlantBatchInput = {
  objectId: string;
  /** Stable catalog/model identifier. Different age stages may use different keys. */
  assetKey: string;
  lod: SceneLod;
  /** Number of independently rendered primitives in this LOD of the glTF model. */
  primitiveCount: number;
};

export type PlantRenderBatch = {
  key: string;
  assetKey: string;
  lod: SceneLod;
  primitiveCount: number;
  objectIds: string[];
};

export type PlantBatchPlan = {
  batches: PlantRenderBatch[];
  instanceCount: number;
  estimatedDrawCalls: number;
};

/**
 * Builds the same batching shape expected from InstancedMesh/EXT_mesh_gpu_instancing.
 * A model with multiple glTF primitives costs one draw call per primitive and
 * chunk; a plant never costs one draw call merely because it is one plant.
 */
export function buildPlantBatchPlan(
  objects: readonly PlantBatchInput[],
  maxInstancesPerBatch: number = SCENE_RENDER_TARGETS.maxInstancesPerBatch,
): PlantBatchPlan {
  if (!Number.isInteger(maxInstancesPerBatch) || maxInstancesPerBatch < 1) {
    throw new RangeError('maxInstancesPerBatch must be a positive integer');
  }
  const grouped = new Map<string, PlantRenderBatch>();
  const seenIds = new Set<string>();

  for (const object of objects) {
    if (!object.objectId || seenIds.has(object.objectId)) throw new Error(`Duplicate or empty scene object id: ${object.objectId}`);
    if (!object.assetKey) throw new Error(`Missing asset key for scene object: ${object.objectId}`);
    if (!Number.isInteger(object.primitiveCount) || object.primitiveCount < 1) {
      throw new RangeError(`Invalid primitive count for scene object: ${object.objectId}`);
    }
    seenIds.add(object.objectId);
    const key = `${object.assetKey}\u0000${object.lod}\u0000${object.primitiveCount}`;
    const batch = grouped.get(key);
    if (batch) batch.objectIds.push(object.objectId);
    else grouped.set(key, { key, assetKey: object.assetKey, lod: object.lod, primitiveCount: object.primitiveCount, objectIds: [object.objectId] });
  }

  const batches = [...grouped.values()].sort((left, right) => left.key.localeCompare(right.key));
  const estimatedDrawCalls = batches.reduce(
    (total, batch) => total + Math.ceil(batch.objectIds.length / maxInstancesPerBatch) * batch.primitiveCount,
    0,
  );
  return { batches, instanceCount: objects.length, estimatedDrawCalls };
}

/** Fallback grouping key used until an exact species/age model is available. */
export function fallbackPlantAssetKey(object: Pick<ScenePlantObject, 'kind' | 'species_revision_id' | 'crown_shape'>) {
  return object.species_revision_id?.trim() || `${object.kind}:${object.crown_shape}`;
}

export type SceneRenderTelemetry = {
  rendererGeneration: number;
  fps: number;
  drawCalls: number;
  triangles: number;
  plantInstances: number;
  visiblePlantInstances: number;
};

export type ScenePerformanceViolation =
  | 'renderer-recreated'
  | 'plant-instances-missing'
  | 'plant-draw-call-budget'
  | 'total-draw-call-budget'
  | 'target-fps'
  | 'dense-fps-floor';

export type ScenePerformanceProfile = 'typical' | 'dense';

export function evaluateSceneTelemetry(
  telemetry: SceneRenderTelemetry,
  options: {
    profile: ScenePerformanceProfile;
    expectedPlantInstances?: number;
    initialRendererGeneration?: number;
    plantDrawCalls?: number;
  },
): ScenePerformanceViolation[] {
  const violations: ScenePerformanceViolation[] = [];
  if (options.initialRendererGeneration !== undefined && telemetry.rendererGeneration !== options.initialRendererGeneration) violations.push('renderer-recreated');
  if (options.expectedPlantInstances !== undefined && telemetry.plantInstances < options.expectedPlantInstances) violations.push('plant-instances-missing');
  if ((options.plantDrawCalls ?? telemetry.drawCalls) > SCENE_RENDER_TARGETS.denseMaxPlantDrawCalls) violations.push('plant-draw-call-budget');
  if (telemetry.drawCalls > SCENE_RENDER_TARGETS.denseMaxTotalDrawCalls) violations.push('total-draw-call-budget');
  if (options.profile === 'typical' && telemetry.fps < SCENE_RENDER_TARGETS.targetFps) violations.push('target-fps');
  if (options.profile === 'dense' && telemetry.fps < SCENE_RENDER_TARGETS.denseMinimumFps) violations.push('dense-fps-floor');
  return violations;
}

export function evaluateDenseSceneTelemetry(
  telemetry: SceneRenderTelemetry,
  options: { initialRendererGeneration?: number; plantDrawCalls?: number } = {},
): ScenePerformanceViolation[] {
  return evaluateSceneTelemetry(telemetry, {
    ...options,
    profile: 'dense',
    expectedPlantInstances: SCENE_RENDER_TARGETS.densePlantCount,
  });
}

/** Stable browser-test surface; values must come from renderer.info/live counters. */
export const SCENE_TELEMETRY_ATTRIBUTES = Object.freeze({
  rendererGeneration: 'data-scene-renderer-generation',
  fps: 'data-scene-fps',
  drawCalls: 'data-scene-draw-calls',
  triangles: 'data-scene-triangles',
  plantInstances: 'data-scene-plant-instances',
  visiblePlantInstances: 'data-scene-visible-plant-instances',
});

export const SCENE_ACCESSIBILITY_CONTRACT = Object.freeze({
  canvasLabel: '3D-сцена посадок',
  focusSelectionLabel: 'Приблизить выбранную посадку',
  resetCameraLabel: 'Показать всю территорию',
  outlineDescription: 'Контур выбранного объекта',
  /** Screen-space outline remains legible and visually stable at any camera angle. */
  outlineWidthPx: 2,
});
