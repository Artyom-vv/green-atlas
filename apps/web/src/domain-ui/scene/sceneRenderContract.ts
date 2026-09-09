import type { ScenePlantObject } from '@green/api-client';

/**
 * Measurable acceptance limits for the browser scene. These values are kept
 * outside the renderer so unit tests, browser diagnostics and the runtime all
 * use the same definition of "smooth enough".
 */
export const SCENE_RENDER_TARGETS = Object.freeze({
  targetFps: 60,
  denseMinimumFps: 30,
  typicalPlantRange: [200, 800] as const,
  densePlantCount: 2_500,
  typicalMaxPlantDrawCalls: 64,
  typicalMaxTotalDrawCalls: 120,
  typicalMaxFrameTriangles: 3_000_000,
  /** Plant models only. Context, shadows and post-processing have a separate budget. */
  denseMaxPlantDrawCalls: 96,
  denseMaxTotalDrawCalls: 180,
  denseMaxFrameTriangles: 6_000_000,
  maxDevicePixelRatio: 1.5,
  maxInstancesPerBatch: 1_024,
});

export const SCENE_QUALITY_BUDGET = Object.freeze({
  renderScales: [1.25, 1, 0.85, 0.7] as const,
  maxDrawingBufferPixels: 1_920 * 1_080,
  degradeP90FrameMs: 18.5,
  recoverP90FrameMs: 14,
  degradeSampleWindowMs: 1_000,
  recoverSampleWindowMs: 3_000,
  changeCooldownMs: 1_000,
  outlineWidthCssPx: 6,
  outlineToleranceCssPx: 0.75,
});

export type SceneRenderScale = (typeof SCENE_QUALITY_BUDGET.renderScales)[number];

export type SceneQualityState = {
  renderScale: SceneRenderScale;
  lastChangedAt: number;
};

export type SceneFrameWindow = {
  p90FrameMs: number;
  durationMs: number;
  measuredAt: number;
};

export function sceneFrameWindow(frameDurationsMs: readonly number[], durationMs: number, measuredAt: number): SceneFrameWindow {
  if (frameDurationsMs.length === 0 || frameDurationsMs.some((duration) => !Number.isFinite(duration) || duration < 0)) {
    throw new RangeError('Frame window requires finite non-negative durations');
  }
  const ordered = [...frameDurationsMs].sort((left, right) => left - right);
  const p90Index = Math.min(ordered.length - 1, Math.ceil(ordered.length * 0.9) - 1);
  return { p90FrameMs: ordered[p90Index], durationMs, measuredAt };
}

/** Pure policy used by browser tests and, eventually, the renderer quality loop. */
export function nextSceneQualityState(state: SceneQualityState, sample: SceneFrameWindow): SceneQualityState {
  const levels = SCENE_QUALITY_BUDGET.renderScales;
  const currentIndex = levels.indexOf(state.renderScale);
  if (currentIndex < 0) throw new RangeError(`Unsupported scene render scale: ${state.renderScale}`);
  if (!Number.isFinite(sample.p90FrameMs) || sample.p90FrameMs < 0) throw new RangeError('p90FrameMs must be finite and non-negative');
  if (sample.measuredAt - state.lastChangedAt < SCENE_QUALITY_BUDGET.changeCooldownMs) return state;

  const shouldDegrade = sample.durationMs >= SCENE_QUALITY_BUDGET.degradeSampleWindowMs
    && sample.p90FrameMs > SCENE_QUALITY_BUDGET.degradeP90FrameMs;
  if (shouldDegrade && currentIndex < levels.length - 1) {
    return { renderScale: levels[currentIndex + 1], lastChangedAt: sample.measuredAt };
  }
  const shouldRecover = sample.durationMs >= SCENE_QUALITY_BUDGET.recoverSampleWindowMs
    && sample.p90FrameMs < SCENE_QUALITY_BUDGET.recoverP90FrameMs;
  if (shouldRecover && currentIndex > 0) {
    return { renderScale: levels[currentIndex - 1], lastChangedAt: sample.measuredAt };
  }
  return state;
}

export type SceneDrawingBuffer = {
  width: number;
  height: number;
  effectivePixelRatio: number;
  renderScale: SceneRenderScale;
};

/** Applies DPR and the quality scale while preserving the hard pixel budget. */
export function sceneDrawingBufferSize(
  cssWidth: number,
  cssHeight: number,
  devicePixelRatio: number,
  renderScale: SceneRenderScale,
): SceneDrawingBuffer {
  if (![cssWidth, cssHeight, devicePixelRatio].every((value) => Number.isFinite(value) && value > 0)) {
    throw new RangeError('Scene viewport dimensions and DPR must be positive finite numbers');
  }
  if (!SCENE_QUALITY_BUDGET.renderScales.includes(renderScale)) throw new RangeError(`Unsupported scene render scale: ${renderScale}`);
  const requestedRatio = Math.min(devicePixelRatio, SCENE_RENDER_TARGETS.maxDevicePixelRatio) * renderScale;
  const requestedPixels = cssWidth * cssHeight * requestedRatio * requestedRatio;
  const pixelBudgetScale = requestedPixels > SCENE_QUALITY_BUDGET.maxDrawingBufferPixels
    ? Math.sqrt(SCENE_QUALITY_BUDGET.maxDrawingBufferPixels / requestedPixels)
    : 1;
  const effectivePixelRatio = requestedRatio * pixelBudgetScale;
  return {
    width: Math.max(1, Math.floor(cssWidth * effectivePixelRatio)),
    height: Math.max(1, Math.floor(cssHeight * effectivePixelRatio)),
    effectivePixelRatio,
    renderScale,
  };
}

export function sceneOutlineBufferWidth(effectivePixelRatio: number) {
  if (!Number.isFinite(effectivePixelRatio) || effectivePixelRatio <= 0) throw new RangeError('effectivePixelRatio must be positive');
  return SCENE_QUALITY_BUDGET.outlineWidthCssPx * effectivePixelRatio;
}

export const SCENE_LOD_THRESHOLDS_PX = Object.freeze({
  /** Above this on-screen diameter the detailed model is useful. */
  near: 160,
  /** Below this diameter a silhouette/billboard is visually sufficient. */
  mid: 32,
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
  /** Adaptive quality level selected by the live frame-time controller. */
  renderScale: SceneRenderScale;
  /** Actual WebGL drawing-buffer ratio after DPR and pixel-budget capping. */
  effectivePixelRatio: number;
  /** Actual post-process outline width in drawing-buffer pixels. */
  outlineWidthBufferPx: number;
  /** Live renderer.info.memory geometries counter. */
  geometries: number;
  /** Live renderer.info.memory textures counter. */
  textures: number;
  /** Live renderer.info.programs length. */
  programs: number;
  /** Number of decoded artist-authored LOD models currently available. */
  loadedPlantModels: number;
  plantInstances: number;
  visiblePlantInstances: number;
};

export type ScenePerformanceViolation =
  | 'renderer-recreated'
  | 'plant-instances-missing'
  | 'plant-count-budget'
  | 'visible-instance-overcount'
  | 'plant-draw-call-budget'
  | 'total-draw-call-budget'
  | 'triangle-budget'
  | 'telemetry-warming-up'
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
  const isTypical = options.profile === 'typical';
  const maxPlantCount = isTypical ? SCENE_RENDER_TARGETS.typicalPlantRange[1] : SCENE_RENDER_TARGETS.densePlantCount;
  const maxPlantDrawCalls = isTypical ? SCENE_RENDER_TARGETS.typicalMaxPlantDrawCalls : SCENE_RENDER_TARGETS.denseMaxPlantDrawCalls;
  const maxTotalDrawCalls = isTypical ? SCENE_RENDER_TARGETS.typicalMaxTotalDrawCalls : SCENE_RENDER_TARGETS.denseMaxTotalDrawCalls;
  const maxTriangles = isTypical ? SCENE_RENDER_TARGETS.typicalMaxFrameTriangles : SCENE_RENDER_TARGETS.denseMaxFrameTriangles;
  if (options.initialRendererGeneration !== undefined && telemetry.rendererGeneration !== options.initialRendererGeneration) violations.push('renderer-recreated');
  if (options.expectedPlantInstances !== undefined && telemetry.plantInstances < options.expectedPlantInstances) violations.push('plant-instances-missing');
  if (telemetry.plantInstances > maxPlantCount) violations.push('plant-count-budget');
  if (telemetry.visiblePlantInstances > telemetry.plantInstances) violations.push('visible-instance-overcount');
  if ((options.plantDrawCalls ?? telemetry.drawCalls) > maxPlantDrawCalls) violations.push('plant-draw-call-budget');
  if (telemetry.drawCalls > maxTotalDrawCalls) violations.push('total-draw-call-budget');
  if (telemetry.triangles > maxTriangles) violations.push('triangle-budget');
  if (telemetry.fps === 0) violations.push('telemetry-warming-up');
  else if (isTypical && telemetry.fps < SCENE_RENDER_TARGETS.targetFps) violations.push('target-fps');
  else if (!isTypical && telemetry.fps < SCENE_RENDER_TARGETS.denseMinimumFps) violations.push('dense-fps-floor');
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
  status: 'data-scene-telemetry-status',
  rendererGeneration: 'data-scene-renderer-generation',
  fps: 'data-scene-fps',
  drawCalls: 'data-scene-draw-calls',
  triangles: 'data-scene-triangles',
  renderScale: 'data-scene-render-scale',
  effectivePixelRatio: 'data-scene-effective-dpr',
  outlineWidthBufferPx: 'data-scene-outline-width-buffer-px',
  geometries: 'data-scene-geometries',
  textures: 'data-scene-textures',
  programs: 'data-scene-programs',
  loadedPlantModels: 'data-scene-loaded-plant-models',
  plantInstances: 'data-scene-plant-instances',
  visiblePlantInstances: 'data-scene-visible-plant-instances',
});

/** Optional diagnostics surface used by the 20-cycle browser leak harness. */
export const SCENE_RESOURCE_TELEMETRY_ATTRIBUTES = Object.freeze({
  activeRenderers: 'data-scene-active-renderers',
  createdRenderers: 'data-scene-created-renderers',
  disposedRenderers: 'data-scene-disposed-renderers',
  geometries: 'data-scene-geometries',
  textures: 'data-scene-textures',
  programs: 'data-scene-programs',
  jsHeapBytes: 'data-scene-js-heap-bytes',
});

export const SCENE_ACCESSIBILITY_CONTRACT = Object.freeze({
  canvasLabel: '3D-сцена посадок',
  focusSelectionLabel: 'Приблизить выбранную посадку',
  resetCameraLabel: 'Показать всю территорию',
  outlineDescription: 'Контур выбранного объекта',
  /** Screen-space outline remains legible and visually stable at any camera angle. */
  outlineWidthPx: 6,
});

export type SceneResourceSnapshot = {
  /** Number of mounted 3D canvases/controllers. More than one is always a leak. */
  activeRenderers: number;
  createdRenderers: number;
  disposedRenderers: number;
  geometries: number;
  textures: number;
  programs: number;
  /** Optional because Firefox and WebKit do not expose performance.memory. */
  jsHeapBytes?: number;
};

export type SceneResourceViolation =
  | 'renderer-overlap'
  | 'renderer-leak'
  | 'geometry-growth'
  | 'texture-growth'
  | 'program-growth'
  | 'heap-growth';

/** A leak must form a positive trend across post-GC samples, not merely a noisy final reading. */
export function hasSceneHeapGrowth(samples: Array<number | undefined>, maxGrowthRatio = 0.05) {
  if (maxGrowthRatio < 0) throw new RangeError('Heap growth tolerance must be non-negative');
  const values = samples.filter((value): value is number => value !== undefined && Number.isFinite(value) && value >= 0);
  if (values.length < 3 || values[0] <= 0) return false;
  const meanX = (values.length - 1) / 2;
  const meanY = values.reduce((sum, value) => sum + value, 0) / values.length;
  let numerator = 0;
  let denominator = 0;
  values.forEach((value, index) => {
    numerator += (index - meanX) * (value - meanY);
    denominator += (index - meanX) ** 2;
  });
  const projectedGrowth = denominator ? Math.max(0, numerator / denominator) * (values.length - 1) : 0;
  // A saw-tooth GC series can have a positive fitted slope even when the
  // final retained heap is effectively identical to the baseline. Require
  // both the trend and an actual end-to-end retained increase.
  const retainedGrowth = values[values.length - 1] - values[0];
  return projectedGrowth > values[0] * maxGrowthRatio
    && retainedGrowth > values[0] * maxGrowthRatio;
}

/**
 * Evaluates the before/after result of repeated 2D↔3D cycles. GPU counters must
 * come from renderer.info before dispose; DOM canvas counts alone are not GPU evidence.
 */
export function evaluateSceneResourceCycles(
  baseline: SceneResourceSnapshot,
  after: SceneResourceSnapshot,
  options: {
    maxHeapGrowthRatio?: number;
    geometryTolerance?: number;
    textureTolerance?: number;
    programTolerance?: number;
  } = {},
): SceneResourceViolation[] {
  const maxHeapGrowthRatio = options.maxHeapGrowthRatio ?? 0.05;
  const geometryTolerance = options.geometryTolerance ?? 0;
  const textureTolerance = options.textureTolerance ?? 0;
  const programTolerance = options.programTolerance ?? 1;
  if (maxHeapGrowthRatio < 0 || geometryTolerance < 0 || textureTolerance < 0 || programTolerance < 0) {
    throw new RangeError('Scene resource tolerances must be non-negative');
  }

  const violations: SceneResourceViolation[] = [];
  if (after.activeRenderers > 1) violations.push('renderer-overlap');
  if (after.activeRenderers !== baseline.activeRenderers
    || after.createdRenderers - baseline.createdRenderers !== after.disposedRenderers - baseline.disposedRenderers) {
    violations.push('renderer-leak');
  }
  if (after.geometries > baseline.geometries + geometryTolerance) violations.push('geometry-growth');
  if (after.textures > baseline.textures + textureTolerance) violations.push('texture-growth');
  if (after.programs > baseline.programs + programTolerance) violations.push('program-growth');
  if (baseline.jsHeapBytes !== undefined && after.jsHeapBytes !== undefined
    && after.jsHeapBytes > baseline.jsHeapBytes * (1 + maxHeapGrowthRatio)) {
    violations.push('heap-growth');
  }
  return violations;
}

export type SceneOutlineSample = {
  azimuthDeg: number;
  polarDeg: number;
  effectivePixelRatio: number;
  outlineWidthBufferPx: number;
};

export type SceneOutlineViolation = 'outline-width' | 'outline-angle-drift';

/** Verifies that post-process outline thickness is invariant in CSS pixels. */
export function evaluateSceneOutline(samples: readonly SceneOutlineSample[]): SceneOutlineViolation[] {
  if (samples.length === 0) return ['outline-width'];
  const cssWidths = samples.map(({ effectivePixelRatio, outlineWidthBufferPx }) => {
    if (!Number.isFinite(effectivePixelRatio) || effectivePixelRatio <= 0 || !Number.isFinite(outlineWidthBufferPx)) return Number.NaN;
    return outlineWidthBufferPx / effectivePixelRatio;
  });
  const violations: SceneOutlineViolation[] = [];
  const { outlineWidthCssPx, outlineToleranceCssPx } = SCENE_QUALITY_BUDGET;
  if (cssWidths.some((width) => !Number.isFinite(width) || Math.abs(width - outlineWidthCssPx) > outlineToleranceCssPx)) {
    violations.push('outline-width');
  }
  const finiteWidths = cssWidths.filter(Number.isFinite);
  if (finiteWidths.length > 1 && Math.max(...finiteWidths) - Math.min(...finiteWidths) > outlineToleranceCssPx) {
    violations.push('outline-angle-drift');
  }
  return violations;
}
