import {
  useAutonomousRunController,
  type AutonomousRunOptions,
} from '@/features/assistant/model/autonomous/useAutonomousRunController';
import {
  AssistantHeader,
  AssistantSurface,
} from '@/features/assistant/ui/shared/AssistantSurface';
import { ControlProvider } from '@green/ui';
import type { FC } from 'react';
import { FormProvider } from 'react-hook-form';
import { getAutonomousPanelView } from './autonomousPanelView';
import { AutonomousRunActions } from './AutonomousRunActions';
import { AutonomousRunContent } from './AutonomousRunContent';
import { RunStatus } from './RunStatus';

export interface AutonomousAgentPanelProps extends AutonomousRunOptions {
  onBack: () => void;
  onClose: () => void;
}

export const AutonomousAgentPanel: FC<AutonomousAgentPanelProps> = ({
  onBack,
  onClose,
  ...options
}) => {
  const controller = useAutonomousRunController(options);
  const view = getAutonomousPanelView(controller, options);

  return (
    <FormProvider {...controller.draftForm}>
      <ControlProvider size="compact">
        <AssistantSurface
          aria-label="Автономный агент"
          header={
            <AssistantHeader
              title="Автономный агент"
              onBack={onBack}
              backLabel="Вернуться к помощнику"
              onClose={onClose}
              closeLabel="Закрыть агента"
            />
          }
          status={<RunStatus {...view.status} />}
          bodyProps={{ ref: controller.historyRef }}
          footer={<AutonomousRunActions {...view.actions} />}
        >
          <AutonomousRunContent {...view.content} />
        </AssistantSurface>
      </ControlProvider>
    </FormProvider>
  );
};
