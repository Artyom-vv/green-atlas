import { queryOptions } from '@tanstack/react-query';
import type { LayerRecognition } from '@green/api-client';
import { preparationApi } from './preparationApi';

export interface RecognitionBinding {
  projectId: string;
  source: string;
  sourceSha?: string | null;
}

export const recognitionKey = ({ projectId, source }: RecognitionBinding) =>
  ['layer-recognition', projectId, source] as const;

export function checkRecognitionSource(
  result: LayerRecognition,
  sourceSha: RecognitionBinding['sourceSha'],
) {
  if (sourceSha !== undefined && result.source_sha256 !== sourceSha)
    throw new Error('Ответ распознавания относится к другому исходнику');
  return result;
}

/** Только подготовка запускает обнаружение задачи: её GET может начать первый проход. */
export function layerRecognitionOptions(
  binding: RecognitionBinding,
  mode: 'discover' | 'observe' = 'discover',
) {
  return queryOptions({
    queryKey: recognitionKey(binding),
    queryFn: async ({ signal }) => {
      const result = await (mode === 'observe'
        ? preparationApi.getLayerRecognitionStatus(binding.projectId, signal)
        : preparationApi.getLayerRecognition(binding.projectId, signal));
      return result === null
        ? null
        : checkRecognitionSource(result, binding.sourceSha);
    },
    staleTime: Infinity,
    // Задача продолжается после ухода с экрана; при возврате нужен актуальный результат.
    refetchOnMount: 'always',
    retry: false,
    networkMode: 'always',
  });
}
