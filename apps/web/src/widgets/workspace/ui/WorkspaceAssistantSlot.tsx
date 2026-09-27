import type { FC } from 'react';
import { ProjectAssistantSidebar } from '@/features/assistant';
import { InlineMessage } from '@green/ui';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

export interface WorkspaceAssistantSlotProps {
  blocked: boolean;
  onClose: () => void;
}

export const WorkspaceAssistantSlotPropsFor = (
  model: Pick<
    WorkspaceReadyModel,
    'manualWork' | 'assistant' | 'setIdeRightTab'
  >,
): WorkspaceAssistantSlotProps => ({
  blocked: model.manualWork.blocksAssistant,
  onClose: () => {
    model.assistant.setOpen(false);
    model.setIdeRightTab('inspector');
  },
});

export const WorkspaceAssistantSlot: FC<WorkspaceAssistantSlotProps> = ({
  blocked,
  onClose,
}) => (
  <>
    <InlineMessage tone="info">
      {blocked
        ? 'Завершите текущий инструмент, чтобы отправить помощнику задачу.'
        : 'Помощник использует текущий проект и выделение.'}
    </InlineMessage>
    <fieldset
      className="flex min-h-0 min-w-0 flex-1 flex-col"
      disabled={blocked}
    >
      <ProjectAssistantSidebar docked onClose={onClose} />
    </fieldset>
  </>
);
