import { useSyncExternalStore, type FC } from 'react';
import { SCENE_RESOURCE_TELEMETRY_ATTRIBUTES } from '@/widgets/scene/model/sceneRenderContract';
import {
  getSceneResourceTelemetrySnapshot,
  subscribeSceneResourceTelemetry,
} from '@/widgets/scene/adapters/three/sceneTelemetryRegistry';

export const SCENE_RESOURCE_DIAGNOSTICS_LABEL = 'Диагностика ресурсов 3D-сцены';

/**
 * Persists outside the lazy 3D view so browser audits can observe disposal
 * after the canvas has left the document.
 */
export const SceneResourceDiagnostics: FC = () => {
  const resources = useSyncExternalStore(
    subscribeSceneResourceTelemetry,
    getSceneResourceTelemetrySnapshot,
    getSceneResourceTelemetrySnapshot,
  );
  return (
    <output
      hidden
      aria-hidden="true"
      aria-label={SCENE_RESOURCE_DIAGNOSTICS_LABEL}
      {...{
        [SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.activeRenderers]:
          resources.activeRenderers,
        [SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.createdRenderers]:
          resources.createdRenderers,
        [SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.disposedRenderers]:
          resources.disposedRenderers,
        [SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.geometries]: resources.geometries,
        [SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.textures]: resources.textures,
        [SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.programs]: resources.programs,
        [SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.jsHeapBytes]:
          resources.jsHeapBytes ?? '',
      }}
    />
  );
};
