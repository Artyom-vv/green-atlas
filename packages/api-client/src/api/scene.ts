import { normalizeScene } from '../adapters/scene';
import type { WireSchema } from '../contracts/wire';
import { request } from '../transport/request';

export const sceneApi = {
  getPlanScene: (
    projectId: string,
    horizonYear: number,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'SceneSnapshot'>>(
      `/api/projects/${projectId}/plan/scene?horizon_year=${horizonYear}`,
      { signal },
    ).then(normalizeScene),
};
