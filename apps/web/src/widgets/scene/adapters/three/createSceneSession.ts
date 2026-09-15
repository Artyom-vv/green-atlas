import { acquirePlantAssetLibrary } from './plantAssets';
import {
  createSceneRenderer,
  SceneController,
  type SceneControllerOptions,
} from './SceneController';
import { registerSceneRenderer } from './sceneTelemetryRegistry';

export interface SceneSessionOptions extends Omit<
  SceneControllerOptions,
  'canvas' | 'assetLibrary' | 'renderer'
> {
  onReady: (controller: SceneController) => void;
  onError: (message: string) => void;
}

/** Owns the renderer, its registered telemetry and one shared-asset lease. */
export function createSceneSession(
  canvas: HTMLCanvasElement,
  options: SceneSessionOptions,
) {
  let renderer: ReturnType<typeof createSceneRenderer>;
  try {
    renderer = createSceneRenderer(canvas);
  } catch (error) {
    console.error('Green Atlas could not create a WebGL renderer', error);
    options.onError(
      '3D недоступен в этом браузере. План остаётся доступен в 2D.',
    );
    return { dispose() {} };
  }
  let live = true;
  let controller: SceneController | undefined;
  let registration: ReturnType<typeof registerSceneRenderer> | undefined;
  let lease: ReturnType<typeof acquirePlantAssetLibrary> | undefined;
  const dispose = () => {
    if (!live) return;
    live = false;
    if (controller) controller.dispose();
    // StrictMode reuses the canvas: forcing context loss would poison remount.
    else renderer.dispose();
    registration?.dispose();
    lease?.release();
  };
  const fail = (error: unknown) => {
    if (!live) return;
    console.error('Green Atlas could not initialise the 3D scene', error);
    dispose();
    options.onError('Не удалось загрузить 3D-сцену. 2D-план сохранён.');
  };
  try {
    lease = acquirePlantAssetLibrary('/assets/plant-models/manifest.json', {
      renderer,
    });
    void lease.library
      .then((assetLibrary) => {
        if (!live) return;
        if (!assetLibrary.loadedModelCount())
          throw new Error('No reviewed plant models could be loaded');
        controller = new SceneController({
          ...options,
          canvas,
          assetLibrary,
          renderer,
          onTelemetry: (telemetry) => {
            if (!live) return;
            registration?.publish(telemetry);
            options.onTelemetry?.(telemetry);
          },
        });
        registration = registerSceneRenderer();
        options.onReady(controller);
      })
      .catch(fail);
  } catch (error) {
    fail(error);
  }
  return { dispose };
}
