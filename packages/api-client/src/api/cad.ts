import * as sdk from '../sdk.generated';
import { API_URL } from '../transport/request';

export const cadApi = {
  getCadSourceAsset: (
    projectId: string,
    operationId: string,
    signal?: AbortSignal,
  ) => sdk.cadAssetMetadata(projectId, operationId, { signal }),
  cadSourceFileUrl: (projectId: string, operationId: string) =>
    `${API_URL}${sdk.getCadAssetFileUrl(projectId, operationId)}`,
};
