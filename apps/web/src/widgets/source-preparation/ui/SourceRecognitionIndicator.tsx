import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from '@green/ui';
import { ScanSearch, LoaderCircle, Check, CircleAlert } from 'lucide-react';
import { useState } from 'react';
import { LayerMappingSummary } from '@/entities/source-data/ui/LayerMappingInformation';
import {
  sourceRecognitionProgressInput,
  type SourcePreparationState,
} from '../model/sourcePreparation';
import { recognitionProgress } from '@/entities/source-data/model/recognitionProgress';
import { RECOGNITION_MESSAGES as messages } from '@/entities/source-data/config/sourceMessages';
import {
  SourceRecognitionProgress,
  type SourceRecognitionProgressProps,
} from './SourceRecognitionProgress';

/** Стабильная кнопка открывает этап, итог назначений и действия проверки. */
export function SourceRecognitionIndicator(
  props: SourceRecognitionProgressProps & {
    mappings?: SourcePreparationState['mappings'];
  },
) {
  const [open, setOpen] = useState(false);
  if (!props.layers.length) return null;
  const progress = recognitionProgress(sourceRecognitionProgressInput(props));
  const showCount =
    progress.total > 0 &&
    ['classifying', 'reviewing', 'completed'].includes(progress.phase) &&
    Boolean(props.layerRecognition);
  const label =
    progress.phase === 'completed' && !props.layerRecognition
      ? messages.assignmentsSaved
      : messages.indicator[progress.phase];
  const statusDescription = showCount
    ? `${label}. ${progress.done} из ${progress.total}`
    : label;
  const needsAttention = ['failed', 'unknown'].includes(progress.phase);
  const icon = progress.busy ? (
    <LoaderCircle className="animate-spin motion-reduce:animate-none" />
  ) : progress.phase === 'completed' ? (
    <Check />
  ) : needsAttention ? (
    <CircleAlert />
  ) : (
    <ScanSearch />
  );
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <Button
              variant="ghost"
              controlSize="default"
              startIcon={icon}
              className={
                needsAttention ? '[&_[data-slot=icon]]:text-warning' : undefined
              }
              data-state={progress.phase}
              aria-label="Слои: распознавание"
              aria-description={statusDescription}
            >
              Слои
            </Button>
          }
        />
        <PopoverPopup
          align="end"
          size="default"
          aria-label="Распознавание слоёв"
        >
          <PopoverTitle className="m-0 mb-3 text-sm font-medium">
            Распознавание слоёв
          </PopoverTitle>
          <SourceRecognitionProgress
            {...props}
            presentation="popover"
            onReview={() => {
              setOpen(false);
              props.onReview?.();
            }}
          >
            {progress.phase === 'completed' && showCount && (
              <dl className="m-0 grid grid-cols-[1fr_auto] gap-4 text-sm leading-5">
                <dt className="text-neutral-500">Распознано</dt>
                <dd className="m-0 tabular-nums">
                  {progress.done} из {progress.total}
                </dd>
              </dl>
            )}
            <LayerMappingSummary
              layers={props.layers}
              mappings={props.recognitionMappings ?? props.mappings ?? {}}
              preview={
                props.recognitionMappings !== undefined &&
                props.recognitionMappings !== props.mappings
              }
              recognition={props.layerRecognition}
            />
          </SourceRecognitionProgress>
        </PopoverPopup>
      </Popover>
      <span className="sr-only" role="status" aria-live="polite">
        {statusDescription}
      </span>
      {progress.busy && (
        <div
          className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-blue-100"
          role="progressbar"
          aria-label="Распознавание слоёв"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress.value}
        >
          <span
            className={
              progress.value === undefined
                ? 'block h-full w-[35%] animate-[progress-indeterminate_1.2s_ease-in-out_infinite] bg-blue-600 motion-reduce:animate-none'
                : 'block h-full bg-blue-600 transition-[width] duration-200 ease-out motion-reduce:transition-none'
            }
            style={{
              width:
                progress.value === undefined ? undefined : `${progress.value}%`,
            }}
          />
        </div>
      )}
    </>
  );
}
