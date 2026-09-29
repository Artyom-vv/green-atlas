import type { FC } from 'react';
import { Button } from '@green/ui';
import { Map as MapIcon, TriangleAlert } from 'lucide-react';
import type { PreparationActionsProps } from './PreparationActions.props';
export const PreparationActions: FC<PreparationActionsProps> = ({
  preparationBlocked,
  reviewOnly,
  calculating,
  statusUnknown,
  readinessBlockedReason,
  readinessSection,
  partialGeometryPending,
  onNeedsReview,
  checkingStatus,
  saveMutation,
  onImport,
  onPlan,
  mapReady,
  mappingsChanged,
  cadPreview = false,
  openEditor,
  calculationPending,
}) => {
  const openPreparedMap =
    cadPreview ||
    (mapReady && !calculationPending && (reviewOnly || (!mappingsChanged && !readinessBlockedReason)));
  const note = !openPreparedMap && !preparationBlocked
    ? readinessBlockedReason ?? (partialGeometryPending
      ? 'Есть пропуски геометрии. Подготовим карту по доступным данным.'
      : undefined)
    : undefined;
  return (
    <div className="grid w-full gap-1">
      <div className="flex flex-wrap items-center justify-end gap-2">
      <Button
        variant="secondary"
        onClick={onImport}
        disabled={preparationBlocked}
      >
        {reviewOnly && !cadPreview ? 'Загрузить полный ZIP' : 'Другой источник'}
      </Button>
      {!reviewOnly && !cadPreview && (!mapReady || calculationPending) && (
        <Button
          variant="secondary"
          loading={openEditor.isPending}
          disabled={preparationBlocked}
          onClick={() => openEditor.mutate()}
        >
          {mappingsChanged
            ? 'Сохранить слои и открыть редактор'
            : 'Открыть редактор без расчёта'}
        </Button>
      )}
        <Button
          variant="primary"
          icon={readinessBlockedReason || partialGeometryPending ? TriangleAlert : MapIcon}
          loading={calculating && !statusUnknown}
          disabled={!cadPreview && preparationBlocked}
          onClick={() => {
            if (openPreparedMap) onPlan();
            else if (readinessBlockedReason) onNeedsReview?.(readinessSection ?? '03');
            else saveMutation.mutate();
          }}
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
      </div>
      <div className="min-h-4 text-right text-xs leading-4 text-amber-800" role={note ? 'status' : undefined}>
        {note}
      </div>
    </div>
  );
};
