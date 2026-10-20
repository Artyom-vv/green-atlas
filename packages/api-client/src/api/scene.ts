import * as sdk from '../sdk.generated';
import { normalizeScene } from '../adapters/scene';

export const sceneApi = {
  getSceneView: sdk.getSceneView,
  prepareSceneRender: sdk.prepareSceneRender,
  getSceneRenderPreparation: sdk.getSceneRenderPreparation,
  createSceneRender: sdk.createSceneRender,
  getSceneRender: sdk.getSceneRender,
  cancelSceneRender: sdk.cancelSceneRender,
  getPlanScene: (
    projectId: string,
    horizonYear: number,
    signal?: AbortSignal,
  ) =>
    sdk
      .getPlanScene(projectId, { horizon_year: horizonYear }, { signal })
      .then(normalizeScene),
};
