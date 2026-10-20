import { ApiClientError } from '@green/api-client';

export const refusedSceneError = (status = 409) =>
  new ApiClientError(
    'SCENE_RENDER_UNAVAILABLE',
    'Scene read refused',
    {},
    {},
    status,
  );
