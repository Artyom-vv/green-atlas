import { useId, type FC } from 'react';
import type { ProjectOperation } from '@green/api-client';
import {
  Button,
  FormActions,
  IconButton,
  InlineMessage,
  Popover,
  PopoverPopup,
  PopoverTrigger,
  Progress,
  Text,
} from '@green/ui';
import { Info } from 'lucide-react';
import { useClock } from '@/shared/time/useClock';
import {
  OPERATION_CLOCK_INTERVAL_MS,
  operationActive,
  operationCount,
  operationElapsed,
  operationRetryable,
} from '../model/operationPresentation';

export interface OperationProgressProps {
  operation: ProjectOperation;
  title: string;
  onCancel?: () => void;
  onRetry?: () => void;
  onDownloadSource?: () => void;
  actionBusy?: boolean;
  retryBlockedReason?: string;
  presentation?: 'section' | 'footer';
  stageLabel?: string;
}

export const OperationProgress: FC<OperationProgressProps> = ({
  operation,
  title,
  onCancel,
  onRetry,
  onDownloadSource,
  actionBusy = false,
  retryBlockedReason,
  presentation = 'section',
  stageLabel,
}) => {
  const active = operationActive(operation);
  const now = useClock(OPERATION_CLOCK_INTERVAL_MS, active);
  const retryReasonId = useId();
  const retryable = operationRetryable(operation);
  const elapsed = operationElapsed(operation, now);
  const count = operationCount(operation);

  if (presentation === 'footer' && !active) {
    return (
      <section className="flex min-w-0 items-center gap-1" aria-label={title}>
        <Text
          variant="caption"
          className="min-w-0 truncate"
          title={operation.stage}
        >
          {operation.stage}
        </Text>
        <Popover>
          <PopoverTrigger
            render={
              <IconButton
                icon={Info}
                label="Сведения о подготовке"
                variant="ghost"
                controlSize="default"
              />
            }
          />
          <PopoverPopup align="end" size="default">
            <OperationProgress
              operation={operation}
              title={title}
              actionBusy={actionBusy}
              onDownloadSource={onDownloadSource}
              onRetry={onRetry}
              retryBlockedReason={retryBlockedReason}
            />
          </PopoverPopup>
        </Popover>
      </section>
    );
  }

  if (presentation === 'footer' && active) {
    return (
      <section
        className="flex min-h-9 min-w-0 items-center gap-x-4 gap-y-2 max-[719px]:flex-wrap"
        aria-label={title}
      >
        <div
          className="min-w-0 flex-1 max-[719px]:basis-full"
          aria-live="polite"
        >
          <Progress
            value={
              operation.progress_mode === 'determinate'
                ? operation.progress
                : undefined
            }
            label={stageLabel ?? operation.stage}
            valueLabel={count}
          />
        </div>
        {elapsed && (
          <Text
            variant="caption"
            className="shrink-0 whitespace-nowrap tabular-nums max-[719px]:ml-auto"
            aria-label={`Прошло ${elapsed}`}
          >
            {elapsed}
          </Text>
        )}
        {onCancel && (
          <Button
            controlSize="default"
            variant="secondary"
            disabled={operation.status === 'cancelling' || actionBusy}
            onClick={onCancel}
          >
            {operation.status === 'cancelling' ? 'Останавливаем' : 'Остановить'}
          </Button>
        )}
      </section>
    );
  }

  return (
    <section className="grid gap-3 py-3" aria-live="polite" aria-label={title}>
      <Text as="h3" variant="heading">
        {title}
      </Text>
      {active ? (
        <Progress
          value={
            operation.progress_mode === 'determinate'
              ? operation.progress
              : undefined
          }
          label={operation.stage}
        />
      ) : (
        <Text as="p" variant="caption">
          {operation.stage}
        </Text>
      )}
      {count || elapsed ? (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
          {count ? (
            <Text mono variant="caption">
              {count}
            </Text>
          ) : null}
          {elapsed ? (
            <Text mono variant="caption">
              Прошло {elapsed}
            </Text>
          ) : null}
        </div>
      ) : null}
      {operation.error ? (
        <InlineMessage
          tone={operation.status === 'interrupted' ? 'warning' : 'error'}
        >
          {operation.error.message}
        </InlineMessage>
      ) : null}
      {active && onCancel ? (
        <FormActions>
          <Button
            controlSize="compact"
            variant="secondary"
            disabled={operation.status === 'cancelling' || actionBusy}
            onClick={onCancel}
          >
            {operation.status === 'cancelling' ? 'Останавливаем' : 'Остановить'}
          </Button>
        </FormActions>
      ) : null}
      {retryable && onRetry && retryBlockedReason ? (
        <Text as="p" id={retryReasonId}>
          {retryBlockedReason}
        </Text>
      ) : null}
      {retryable && (onRetry || onDownloadSource) ? (
        <FormActions>
          {onDownloadSource ? (
            <Button
              controlSize="compact"
              variant="ghost"
              disabled={actionBusy}
              onClick={onDownloadSource}
            >
              Скачать исходный DXF
            </Button>
          ) : null}
          {onRetry ? (
            <Button
              controlSize="compact"
              variant="secondary"
              loading={actionBusy}
              disabled={Boolean(retryBlockedReason)}
              aria-describedby={retryBlockedReason ? retryReasonId : undefined}
              onClick={onRetry}
            >
              Запустить повторно
            </Button>
          ) : null}
        </FormActions>
      ) : null}
    </section>
  );
};
