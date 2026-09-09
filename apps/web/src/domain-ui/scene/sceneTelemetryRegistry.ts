import type { SceneRenderTelemetry, SceneResourceSnapshot } from './sceneRenderContract';

type SceneRendererRegistration = {
  publish: (telemetry: SceneRenderTelemetry) => void;
  dispose: () => void;
};

type MemoryPerformance = Performance & {
  memory?: { usedJSHeapSize?: number };
};

const listeners = new Set<() => void>();
const active = new Map<number, SceneRenderTelemetry | undefined>();
let nextRegistrationId = 0;
let createdRenderers = 0;
let disposedRenderers = 0;

function measuredHeapBytes() {
  if (typeof performance === 'undefined') return undefined;
  const value = (performance as MemoryPerformance).memory?.usedJSHeapSize;
  return Number.isFinite(value) && value !== undefined && value >= 0 ? value : undefined;
}

function buildSnapshot(): SceneResourceSnapshot {
  let geometries = 0;
  let textures = 0;
  let programs = 0;
  for (const telemetry of active.values()) {
    if (!telemetry) continue;
    geometries += Number.isFinite(telemetry.geometries) ? telemetry.geometries : 0;
    textures += Number.isFinite(telemetry.textures) ? telemetry.textures : 0;
    programs += Number.isFinite(telemetry.programs) ? telemetry.programs : 0;
  }
  const jsHeapBytes = measuredHeapBytes();
  return {
    activeRenderers: active.size,
    createdRenderers,
    disposedRenderers,
    geometries,
    textures,
    programs,
    ...(jsHeapBytes === undefined ? {} : { jsHeapBytes }),
  };
}

let snapshot = buildSnapshot();

function publishSnapshot() {
  snapshot = buildSnapshot();
  listeners.forEach((listener) => listener());
}

/**
 * Registers a successfully constructed WebGL renderer. The returned handle is
 * idempotent so React cleanup and failed async completions cannot double-count
 * a disposal.
 */
export function registerSceneRenderer(): SceneRendererRegistration {
  const id = ++nextRegistrationId;
  let disposed = false;
  createdRenderers += 1;
  active.set(id, undefined);
  publishSnapshot();
  return {
    publish(telemetry) {
      if (disposed) return;
      active.set(id, telemetry);
      publishSnapshot();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      active.delete(id);
      disposedRenderers += 1;
      publishSnapshot();
    },
  };
}

export function subscribeSceneResourceTelemetry(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getSceneResourceTelemetrySnapshot() {
  return snapshot;
}

/** Test isolation only; production lifecycle uses registerSceneRenderer(). */
export function resetSceneTelemetryRegistryForTests() {
  active.clear();
  nextRegistrationId = 0;
  createdRenderers = 0;
  disposedRenderers = 0;
  publishSnapshot();
}
