import * as sdk from '../sdk.generated';
import { API_URL, json } from '../transport/request';
import { readResource } from '../transport/readResource';

export const releaseApi = {
  createExport: (projectId: string) => sdk.createExport(projectId, json()),
  createRelease: sdk.createRelease,
  getRelease: (projectId: string, releaseId: string) =>
    readResource(
      (options) => sdk.getRelease(projectId, releaseId, options),
      'release',
    ),
  downloadUrl: (path: string) => `${API_URL}${path}`,
};
