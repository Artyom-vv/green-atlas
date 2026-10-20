import { LayerRecognitionStatus } from '@/entities/source-data/ui/LayerRecognitionStatus';
import {
  sourceRecognitionProgressInput,
  type SourcePreparationState,
} from '../model/sourcePreparation';
import type { ReactNode } from 'react';
import { RECOGNITION_MESSAGES as messages } from '@/entities/source-data/config/sourceMessages';

export type SourceRecognitionProgressProps = Pick<
  SourcePreparationState,
  | 'layers'
  | 'layerRecognition'
  | 'allSavedLayersConfirmed'
  | 'layerRecognitionQuery'
  | 'retryLayerRecognition'
> & {
  autoApplyingRecognition?: boolean;
  recognitionMappings?: SourcePreparationState['mappings'];
  recognitionSaveRecovery?: SourcePreparationState['recognitionSaveRecovery'];
  onReview?: () => void;
  presentation?: 'default' | 'popover';
  showCount?: boolean;
  children?: ReactNode;
};

/** Состояние задачи остаётся видимым вне закрываемого раздела слоёв. */
export function SourceRecognitionProgress(
  props: SourceRecognitionProgressProps,
) {
  const {
    layers,
    layerRecognition,
    allSavedLayersConfirmed,
    retryLayerRecognition,
    autoApplyingRecognition,
    onReview,
    presentation = 'default',
    showCount,
    children,
  } = props;
  if (
    !layers.length ||
    (presentation !== 'popover' &&
      allSavedLayersConfirmed &&
      !layerRecognition &&
      !autoApplyingRecognition)
  )
    return null;
  return (
    <div
      className={presentation === 'popover' ? undefined : 'px-4 py-3 sm:px-6'}
    >
      <LayerRecognitionStatus
        variant={presentation}
        showCount={showCount}
        layers={layers}
        {...sourceRecognitionProgressInput(props)}
        onRetry={() =>
          props.recognitionSaveRecovery
            ? props.recognitionSaveRecovery.onRetry()
            : retryLayerRecognition.mutate()
        }
        onReview={onReview}
        reviewLabel={
          allSavedLayersConfirmed ? messages.openLayers : messages.review
        }
      >
        {children}
      </LayerRecognitionStatus>
    </div>
  );
}
