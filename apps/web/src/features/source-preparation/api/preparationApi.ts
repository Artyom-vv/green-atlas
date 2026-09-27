import { api } from '@green/api-client';

// Bind only this scenario's ports; transport and wire contracts remain in the client.
export const preparationApi = {
  getNativeFaces: (...args: Parameters<typeof api.getNativeFaces>) =>
    api.getNativeFaces(...args),
  decideNativeFace: (...args: Parameters<typeof api.decideNativeFace>) =>
    api.decideNativeFace(...args),
  getSourceObjectContext: (
    ...args: Parameters<typeof api.getSourceObjectContext>
  ) => api.getSourceObjectContext(...args),
  getSourceReadIssues: (...args: Parameters<typeof api.getSourceReadIssues>) =>
    api.getSourceReadIssues(...args),
  checkSourceAreaGroup: (
    ...args: Parameters<typeof api.checkSourceAreaGroup>
  ) => api.checkSourceAreaGroup(...args),
  acceptSourceAreaGroup: (
    ...args: Parameters<typeof api.acceptSourceAreaGroup>
  ) => api.acceptSourceAreaGroup(...args),
  removeSourceAreaGroup: (
    ...args: Parameters<typeof api.removeSourceAreaGroup>
  ) => api.removeSourceAreaGroup(...args),
  getSourceObjectReview: (
    ...args: Parameters<typeof api.getSourceObjectReview>
  ) => api.getSourceObjectReview(...args),
  decideSourceObject: (...args: Parameters<typeof api.decideSourceObject>) =>
    api.decideSourceObject(...args),
  getLayerRecognition: (projectId: string) =>
    api.getLayerRecognition(projectId),
  retryLayerRecognition: (projectId: string) =>
    api.retryLayerRecognition(projectId),
  getNativeAreaPreview: (projectId: string, proposalId: string) =>
    api.getNativeAreaPreview(projectId, proposalId),
  acceptPartialGeometry: (
    ...args: Parameters<typeof api.acceptPartialGeometry>
  ) => api.acceptPartialGeometry(...args),
  decideNativeArea: (...args: Parameters<typeof api.decideNativeArea>) =>
    api.decideNativeArea(...args),
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
