/** Модель подготовки исходника связывает серверную задачу и запись назначений с её представлением. */
import type { useSourcePreparation } from '@/features/source-preparation';
import type { RecognitionProgressInput } from '@/entities/source-data/model/recognitionProgress';

export type SourcePreparationState = ReturnType<typeof useSourcePreparation>;

type RecognitionState = Pick<
  SourcePreparationState,
  | 'layerRecognition'
  | 'layerRecognitionQuery'
  | 'retryLayerRecognition'
  | 'allSavedLayersConfirmed'
> & {
  autoApplyingRecognition?: boolean;
  recognitionSaveRecovery?: SourcePreparationState['recognitionSaveRecovery'];
};

/** Кнопка и открытая сводка наблюдают одно чтение, задачу и запись назначений. */
export function sourceRecognitionProgressInput(
  state: RecognitionState,
): RecognitionProgressInput {
  const { layerRecognition, layerRecognitionQuery, retryLayerRecognition } =
    state;
  return {
    recognition: layerRecognition,
    loading: layerRecognitionQuery.isLoading,
    error:
      layerRecognitionQuery.isError ||
      retryLayerRecognition.isError ||
      Boolean(
        state.recognitionSaveRecovery && !state.recognitionSaveRecovery.loading,
      ),
    retrying: retryLayerRecognition.isPending,
    checkingStatus:
      retryLayerRecognition.checkingStatus ||
      (layerRecognitionQuery.isFetching &&
        Boolean(layerRecognition) &&
        layerRecognition?.status !== 'running'),
    saving:
      state.autoApplyingRecognition || state.recognitionSaveRecovery?.loading,
    noKnownJob: retryLayerRecognition.noKnownJob,
    assignmentsSaved: state.allSavedLayersConfirmed,
  };
}
