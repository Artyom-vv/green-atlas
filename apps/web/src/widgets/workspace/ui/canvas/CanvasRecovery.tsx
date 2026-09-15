import { errorMessage } from '@/shared/errors/errorMessage';
import { CommandRecovery } from '@/shared/ui/CommandRecovery';
import { InlineMessage } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasRecoveryProps extends Pick<
  WorkspaceReadyModel,
  'zoneCommands' | 'deleteObjects' | 'applyChanges' | 'historyCommands'
> {}

export const CanvasRecoveryPropsFor = (props: CanvasRecoveryProps) => ({
  zoneCommands: props.zoneCommands,
  deleteObjects: props.deleteObjects,
  applyChanges: props.applyChanges,
  historyCommands: props.historyCommands,
});

/** Recovery stays below view controls, within one stack instead of overlapping windows. */
export const CanvasRecovery: FC<CanvasRecoveryProps> = ({
  zoneCommands,
  deleteObjects,
  applyChanges,
  historyCommands,
}) => (
  <div className="absolute top-16 right-4 z-40 grid max-h-[calc(100%-8rem)] w-80 max-w-[calc(100%-6rem)] gap-2 overflow-auto">
    {zoneCommands.notice && (
      <InlineMessage tone="info">{zoneCommands.notice}</InlineMessage>
    )}
    {deleteObjects.notice && (
      <InlineMessage tone="info">{deleteObjects.notice}</InlineMessage>
    )}
    {deleteObjects.recovery && (
      <CommandRecovery
        title="Обновление плана после удаления"
        onRetry={deleteObjects.recovery.onRetry}
        loading={deleteObjects.recovery.loading}
      >
        {deleteObjects.recovery.message}
      </CommandRecovery>
    )}
    {zoneCommands.needsRefresh && (
      <CommandRecovery
        title="Необходимо обновить участки"
        actionLabel="Обновить участки"
        onRetry={zoneCommands.retryRefresh}
      >
        {zoneCommands.recoveryError
          ? errorMessage(zoneCommands.recoveryError)
          : 'Проверьте состояние проекта перед продолжением.'}
      </CommandRecovery>
    )}
    {applyChanges.needsRefresh && (
      <CommandRecovery
        title="Изменения сохранены"
        onRetry={applyChanges.retryRefresh}
      >
        Не удалось обновить данные проекта.
      </CommandRecovery>
    )}
    {historyCommands.recovery && (
      <CommandRecovery
        title="Состояние истории"
        actionLabel="Обновить историю"
        onRetry={historyCommands.recovery.onRetry}
        loading={historyCommands.recovery.loading}
      >
        {historyCommands.recovery.message}
      </CommandRecovery>
    )}
  </div>
);
