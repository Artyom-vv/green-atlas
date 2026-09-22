import { api } from '@green/api-client';

// Bind only this scenario's ports; transport and wire contracts remain in the client.
export const preparationApi = {
  acceptPartialGeometry: (
    ...args: Parameters<typeof api.acceptPartialGeometry>
  ) => api.acceptPartialGeometry(...args),
  openSourceEditor: (projectId: string) => api.openSourceEditor(projectId),
  getProject: (...args: Parameters<typeof api.getProject>) =>
    api.getProject(...args),
  getDataPassport: (...args: Parameters<typeof api.getDataPassport>) =>
    api.getDataPassport(...args),
  getLatestOperation: (...args: Parameters<typeof api.getLatestOperation>) =>
    api.getLatestOperation(...args),
  getOperation: (...args: Parameters<typeof api.getOperation>) =>
    api.getOperation(...args),
  saveMappings: (...args: Parameters<typeof api.saveMappings>) =>
    api.saveMappings(...args),
  startGeometryOperation: (
    ...args: Parameters<typeof api.startGeometryOperation>
  ) => api.startGeometryOperation(...args),
  cancelOperation: (...args: Parameters<typeof api.cancelOperation>) =>
    api.cancelOperation(...args),
  sourceDownloadUrl: (...args: Parameters<typeof api.sourceDownloadUrl>) =>
    api.sourceDownloadUrl(...args),
};
