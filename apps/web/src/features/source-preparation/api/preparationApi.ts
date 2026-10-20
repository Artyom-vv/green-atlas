import { api, type Project } from '@green/api-client';
import { queryOptions } from '@tanstack/react-query';

// Сценарий использует свои API-методы; транспорт и сетевые типы принадлежат клиенту.
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
  getLayerRecognition: (...args: Parameters<typeof api.getLayerRecognition>) =>
    api.getLayerRecognition(...args),
  getLayerRecognitionStatus: (
    ...args: Parameters<typeof api.getLayerRecognitionStatus>
  ) => api.getLayerRecognitionStatus(...args),
  subscribeLayerRecognition: (
    ...args: Parameters<typeof api.subscribeLayerRecognition>
  ) => api.subscribeLayerRecognition(...args),
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
  getProjectRead: (...args: Parameters<typeof api.getProjectRead>) =>
    api.getProjectRead(...args),
  getDataPassport: (...args: Parameters<typeof api.getDataPassport>) =>
    api.getDataPassport(...args),
  getLatestOperation: (...args: Parameters<typeof api.getLatestOperation>) =>
    api.getLatestOperation(...args),
  getOperation: (...args: Parameters<typeof api.getOperation>) =>
    api.getOperation(...args),
  subscribeOperation: (...args: Parameters<typeof api.subscribeOperation>) =>
    api.subscribeOperation(...args),
  saveMappings: (...args: Parameters<typeof api.saveMappings>) =>
    api.saveMappings(...args),
  startCapturePreparation: (projectId: string) =>
    api.startCapturePreparation(projectId),
  startGeometryOperation: (
    ...args: Parameters<typeof api.startGeometryOperation>
  ) => api.startGeometryOperation(...args),
  cancelOperation: (...args: Parameters<typeof api.cancelOperation>) =>
    api.cancelOperation(...args),
  sourceDownloadUrl: (...args: Parameters<typeof api.sourceDownloadUrl>) =>
    api.sourceDownloadUrl(...args),
};

/** Паспорт относится к ревизии проекта; закрытый раздел не инициирует чтение */
export const sourceDataPassportQuery = (
  project: Project | undefined,
  enabled: boolean,
) =>
  queryOptions({
    queryKey: [
      'data-passport',
      project?.id,
      project?.state_version,
      project?.source_file?.content_sha256,
      project?.source_file?.imported_at,
    ],
    queryFn: ({ signal }) =>
      preparationApi.getDataPassport(project!.id!, { signal }),
    enabled: Boolean(project?.id && enabled),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    networkMode: 'always',
  });
