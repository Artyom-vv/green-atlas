import type { Project } from '@green/api-client';
import { RECOGNITION_MESSAGES as messages } from '@/entities/source-data/config/sourceMessages';
import { recognitionProgress } from '@/entities/source-data/model/recognitionProgress';
import { useLayerRecognitionObservation } from './useLayerRecognition';
import { sourceIdentity } from './sourcePreparation';

export interface WorkspaceRecognitionStatus {
  label: string;
  state: 'complete' | 'running' | 'failed' | 'unknown';
}

/** Reading this status never starts or resumes a recognition command. */
export function useWorkspaceRecognitionActivity(
  project: Project,
): WorkspaceRecognitionStatus | undefined {
  const binding = {
    projectId: project.id ?? '',
    source: sourceIdentity(project),
    sourceSha: project.source_file?.content_sha256,
  };
  const query = useLayerRecognitionObservation(
    binding,
    'observe',
    Boolean(binding.projectId && binding.sourceSha),
  );
  if (
    (!query.data && !query.isError) ||
    (query.data?.status === 'unconfigured' && !query.isError)
  )
    return undefined;
  const { phase, done, total } = recognitionProgress({
    recognition: query.data ?? undefined,
    error: query.isError,
    checkingStatus: query.isFetching && query.data?.status !== 'running',
  });
  const label =
    phase === 'classifying'
      ? `Распознаём слои: ${done} из ${total}`
      : phase === 'reviewing'
        ? `${messages.phases.reviewing.title}: ${done} из ${total}`
        : phase === 'publishing'
          ? messages.phases.publishing.title
          : phase === 'finishing'
            ? 'Завершаем распознавание'
            : phase === 'failed'
              ? 'Распознавание приостановлено'
              : phase === 'unknown'
                ? 'Статус распознавания недоступен'
                : phase === 'completed'
                  ? 'Предложения по слоям готовы'
                  : 'Проверяем распознавание';
  return {
    label,
    state:
      query.isError || phase === 'unknown'
        ? 'unknown'
        : query.data?.status === 'running'
          ? 'running'
          : phase === 'failed'
            ? 'failed'
            : phase === 'completed'
              ? 'complete'
              : 'unknown',
  };
}
