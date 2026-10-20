/** Действия подготовки: готовые назначения ведут к карте, незавершённые — к проверке исходника */
import type { FC } from 'react';
import {
  Button,
  IconButton,
  Popover,
  PopoverTrigger,
  PopoverPopup,
} from '@green/ui';
import { Info, Undo2, Map as MapIcon, TriangleAlert } from 'lucide-react';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
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
  capturePreparationNeeded,
  prepareCapture,
  onImport,
  onPlan,
  mapReady,
  mappingsChanged,
  physicalMappingsChanged,
  localMapAvailable,
  cadPreview = false,
  openEditor,
  autoApplyingRecognition,
  calculationPending,
  canUndoMappings,
  undoMappings,
  status,
}) => {
  const openPreparedMap =
    cadPreview ||
    (mapReady &&
      !calculationPending &&
      (reviewOnly ||
        (localMapAvailable &&
          !physicalMappingsChanged &&
          !readinessBlockedReason)));
  const inspectCaptureFirst = capturePreparationNeeded;
  const saveLocalChanges = mappingsChanged && !physicalMappingsChanged;
  const primaryPending =
    calculating &&
    !openEditor.isPending &&
    !autoApplyingRecognition &&
    !saveMutation.readRecovery;
  const note =
    !openPreparedMap && !preparationBlocked
      ? ((inspectCaptureFirst ? undefined : readinessBlockedReason) ??
        (mapReady && !localMapAvailable
          ? PREPARATION_MESSAGES.unavailableMap
          : undefined) ??
        (partialGeometryPending ? PREPARATION_MESSAGES.partialMap : undefined))
      : undefined;
  return (
    <div className="flex items-center justify-between gap-4 max-[719px]:flex-wrap max-[719px]:gap-2">
      <div className="-ml-3 flex flex-wrap items-center gap-2">
        {!reviewOnly && !cadPreview && (!mapReady || calculationPending) && (
          <Button
            variant="ghost"
            controlSize="default"
            loading={openEditor.isPending}
            disabled={preparationBlocked}
            onClick={() => openEditor.mutate()}
          >
            Открыть без расчёта
          </Button>
        )}
        <Button
          variant="ghost"
          controlSize="default"
          onClick={onImport}
          disabled={preparationBlocked}
        >
          Другой чертёж
        </Button>
        {!reviewOnly && canUndoMappings && (
          <Button
            variant="ghost"
            controlSize="default"
            startIcon={<Undo2 />}
            disabled={preparationBlocked}
            onClick={undoMappings}
          >
            Отменить действие
          </Button>
        )}
      </div>
      <div className="flex min-w-0 items-center gap-2 max-[719px]:ml-auto">
        {status}
        {note && (
          <Popover>
            <PopoverTrigger
              render={
                <IconButton
                  label="Условия подготовки"
                  icon={Info}
                  variant="ghost"
                  controlSize="default"
                />
              }
            />
            <PopoverPopup align="end" size="default">
              {note}
            </PopoverPopup>
          </Popover>
        )}
        <Button
          variant="primary"
          controlSize="default"
          icon={
            readinessBlockedReason || partialGeometryPending
              ? TriangleAlert
              : MapIcon
          }
          loading={primaryPending && !statusUnknown}
          disabled={!cadPreview && preparationBlocked}
          onClick={() => {
            if (inspectCaptureFirst) prepareCapture();
            else if (saveLocalChanges) saveMutation.mutate();
            else if (openPreparedMap) onPlan();
            else if (readinessBlockedReason)
              onNeedsReview?.(readinessSection ?? '03');
            else saveMutation.mutate();
          }}
        >
          {statusUnknown
            ? 'Статус расчёта неизвестен'
            : checkingStatus
              ? 'Проверяем состояние'
              : primaryPending
                ? (saveMutation.pendingLabel ?? 'Готовим карту')
                : reviewOnly
                  ? 'Открыть для просмотра'
                  : inspectCaptureFirst
                    ? 'Подготовить чертёж'
                    : saveLocalChanges
                      ? localMapAvailable &&
                        mapReady &&
                        !calculationPending &&
                        !readinessBlockedReason
                        ? 'Сохранить и открыть карту'
                        : mapReady || readinessBlockedReason
                          ? 'Сохранить изменения'
                          : 'Подготовить карту'
                      : openPreparedMap
                        ? 'Открыть карту'
                        : partialGeometryPending
                          ? 'Подготовить по доступным объектам'
                          : 'Подготовить карту'}
        </Button>
      </div>
    </div>
  );
};
