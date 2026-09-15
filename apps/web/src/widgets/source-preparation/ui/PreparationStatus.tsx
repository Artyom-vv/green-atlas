import type { FC } from 'react';
import { Button, InlineMessage, Progress } from '@green/ui';
import { OperationProgress } from '@/entities/operation/ui/OperationProgress';
import type { PreparationStatusProps } from './PreparationStatus.props';
export const PreparationStatus: FC<PreparationStatusProps> = ({
  preparationRecovery,
  statusUnknown,
  operation,
  statusQuery,
  checkingStatus,
  previousSourceOperation,
  cancelOperation,
  saveMutation,
  readinessBlockedReason,
  downloadSource,
}) => (
  <>
    {' '}
    {preparationRecovery && (
      <InlineMessage tone="warning" title="Восстановление подготовки">
        <div className="flex flex-wrap items-center gap-3">
          <span>{preparationRecovery.message}</span>
          <Button
            variant="secondary"
            loading={preparationRecovery.loading}
            onClick={preparationRecovery.onRetry}
          >
            Проверить состояние проекта
          </Button>
        </div>
      </InlineMessage>
    )}
    {statusUnknown ? (
      <InlineMessage tone="error" title="Не удалось узнать состояние расчёта">
        <p>
          Сначала восстановите статус, чтобы продолжить подготовку карты.
          Повторная загрузка статуса не запускает новый расчёт.
        </p>
        {operation && <p>Последний полученный этап: {operation.stage}.</p>}
        <Button
          variant="secondary"
          loading={statusQuery.isFetching}
          onClick={() => void statusQuery.refetch()}
        >
          Повторить загрузку статуса
        </Button>
      </InlineMessage>
    ) : checkingStatus ? (
      <Progress label="Проверяем состояние подготовки карты" />
    ) : (
      operation && (
        <div className="sticky -top-6 z-10 border-b border-neutral-200 bg-white py-3">
          {previousSourceOperation && (
            <InlineMessage tone="info">
              Завершается расчёт предыдущего исходника. Дождитесь остановки или
              остановите его перед подготовкой нового файла.
            </InlineMessage>
          )}
          <OperationProgress
            operation={operation}
            title="Подготовка карты"
            actionBusy={cancelOperation.isPending || saveMutation.isPending}
            retryBlockedReason={readinessBlockedReason}
            onCancel={() => cancelOperation.mutate(operation.id!)}
            onRetry={() => saveMutation.mutate()}
            onDownloadSource={() => {
              window.location.href = downloadSource();
            }}
          />
        </div>
      )
    )}
  </>
);
