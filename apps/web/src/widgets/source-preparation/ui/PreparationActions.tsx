import type { FC } from 'react';
import { Button } from '@green/ui';
import { Map as MapIcon } from 'lucide-react';
import type { PreparationActionsProps } from './PreparationActions.props';
export const PreparationActions: FC<PreparationActionsProps> = ({
  preparationBlocked,
  reviewOnly,
  calculating,
  statusUnknown,
  readinessBlockedReason,
  checkingStatus,
  saveMutation,
  onImport,
  onPlan,
  mapReady,
  mappingsChanged,
  cadPreview = false,
}) => {
  const openPreparedMap =
    cadPreview || (mapReady && (reviewOnly || !mappingsChanged));
  return (
    <>
      <Button
        variant="secondary"
        onClick={onImport}
        disabled={preparationBlocked}
      >
        {reviewOnly && !cadPreview ? 'Загрузить полный ZIP' : 'Другой источник'}
      </Button>
      <Button
        variant="primary"
        icon={MapIcon}
        loading={calculating && !statusUnknown}
        disabled={
          !cadPreview &&
          (preparationBlocked ||
            (!openPreparedMap && Boolean(readinessBlockedReason)))
        }
        onClick={() => (openPreparedMap ? onPlan() : saveMutation.mutate())}
      >
        {statusUnknown
          ? 'Статус расчёта неизвестен'
          : checkingStatus
            ? 'Проверяем состояние'
            : calculating
              ? 'Готовим карту'
              : reviewOnly
                ? 'Открыть для просмотра'
                : openPreparedMap
                  ? 'Открыть карту'
                  : 'Подготовить карту'}
      </Button>
    </>
  );
};
