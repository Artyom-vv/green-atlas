import { errorText } from '@/features/assistant/model/autonomous/presentation';
import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { type AutonomousRunOptions } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
export interface RunApprovalProps
  extends
    Pick<
      AutonomousRunController,
      | 'approval'
      | 'run'
      | 'zoneChange'
      | 'existing'
      | 'zoneRequested'
      | 'result'
      | 'previewState'
      | 'busy'
      | 'decisionDisabled'
      | 'retry'
      | 'zonePreview'
      | 'previewUnavailable'
      | 'approve'
      | 'stopping'
      | 'cancel'
    >,
    Pick<AutonomousRunOptions, 'onPreviewChange'> {}
export const RunApproval: FC<RunApprovalProps> = ({
  approval,
  run,
  zoneChange,
  existing,
  zoneRequested,
  result,
  previewState,
  busy,
  decisionDisabled,
  retry,
  zonePreview,
  previewUnavailable,
  approve,
  stopping,
  cancel,
  onPreviewChange,
}) => (
  <>
    {!!(approval && run) && (
      <section
        aria-label="Подтверждение изменения"
        className="grid shrink-0 gap-2 border-t border-neutral-300 bg-white p-3"
      >
        <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
          {zoneChange
            ? `${zoneChange.title}?`
            : existing
              ? `${existing.title}?`
              : zoneRequested
                ? 'Проверить изменение участка'
                : 'Изменить план?'}
        </Text>
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {result || existing || zoneChange
            ? 'Применится только подготовленное предложение.'
            : 'Проверьте подготовленное изменение перед применением.'}
        </Text>
        {!!((onPreviewChange || zoneRequested) && previewState.loading) && (
          <Text
            role="status"
            as="p"
            variant="body"
            className="m-0 wrap-anywhere"
          >
            Загружаем предложение на карту
          </Text>
        )}
        {!!previewState.error && (
          <>
            <Text
              role="alert"
              as="p"
              variant="body"
              className="m-0 wrap-anywhere"
            >
              {errorText(previewState.error)}
            </Text>
            <Button
              type="button"
              variant="secondary"
              loading={busy}
              disabled={decisionDisabled}
              onClick={() => void retry()}
            >
              Пересчитать предложение
            </Button>
          </>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant={
              existing?.action === 'delete' ||
              zonePreview?.operation === 'delete'
                ? 'danger'
                : 'primary'
            }
            loading={busy}
            disabled={previewUnavailable || decisionDisabled}
            onClick={() => void approve()}
          >
            {zoneChange?.title ?? existing?.title ?? 'Применить предложение'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            loading={stopping}
            disabled={decisionDisabled}
            onClick={() => void cancel()}
          >
            Отклонить предложение
          </Button>
        </div>
      </section>
    )}
  </>
);
