import { useProjectAssistant } from '@/features/assistant/model/assistantContext';
import { featureAvailability } from '@/shared/config/featureAvailability';
import { ControlProvider, ResizeHandle } from '@green/ui';
import { useEffect, useState, type FC } from 'react';
import { useLocation } from 'react-router-dom';
import { useConversationViewport } from '../model/conversation/useConversationViewport';
import { AutonomousAgentPanel } from './autonomous/AutonomousAgentPanel';
import { ConversationSurface } from './conversation/ConversationSurface';

export interface ProjectAssistantSidebarProps {
  docked?: boolean;
  onClose?: () => void;
}

const AssistantSidebar: FC<ProjectAssistantSidebarProps> = ({
  docked = false,
  onClose,
}) => {
  const chat = useProjectAssistant();
  const { pathname } = useLocation();
  const { history, composer, follow, onScroll } = useConversationViewport(chat);
  const [mode, setMode] = useState<'legacy' | 'autonomous'>('legacy');
  const syncMode = chat.setAssistantMode;
  useEffect(() => {
    syncMode?.(mode);
  }, [mode, syncMode]);
  const onMap =
    pathname.endsWith('/workspace') ||
    (docked && pathname.endsWith('/workspace/ide'));
  const close = () => {
    chat.setOpen(false);
    onClose?.();
    requestAnimationFrame(() =>
      document
        .querySelector<HTMLButtonElement>('[data-assistant-trigger]')
        ?.focus(),
    );
  };
  return (
    <ControlProvider size="compact">
      <aside
        id="project-assistant"
        className="relative flex h-full min-h-0 min-w-0 flex-col bg-white"
        aria-label={mode === 'autonomous' ? 'Автономный агент' : 'Чат проекта'}
        hidden={!chat.open}
      >
        {mode === 'autonomous' ? (
          <AutonomousAgentPanel
            key={chat.projectId}
            projectId={chat.projectId}
            selectionContext={
              onMap && chat.project?.id === chat.projectId
                ? {
                    project_id: chat.projectId,
                    state_version: chat.project.state_version,
                    plan_version: chat.project.plan?.version ?? null,
                    object_ids: chat.selectedIds,
                    zone_ids: chat.selectedZoneIds,
                  }
                : undefined
            }
            onMapControl={chat.executeMapControl}
            onPreviewChange={chat.setAutonomousPreview}
            onBack={() => setMode('legacy')}
            onClose={close}
          />
        ) : (
          <ConversationSurface
            chat={chat}
            onMap={onMap}
            onAgent={() => setMode('autonomous')}
            close={close}
            history={history}
            composer={composer}
            follow={follow}
            onScroll={onScroll}
          />
        )}
        {!docked && (
          <ResizeHandle
            className="absolute inset-y-0 -right-1"
            label="Ширина чата"
            orientation="vertical"
            value={chat.width}
            min={chat.minWidth}
            max={chat.maxWidth}
            onChange={chat.setWidth}
            onReset={chat.resetWidth}
          />
        )}
      </aside>
    </ControlProvider>
  );
};

export const ProjectAssistantSidebar: FC<ProjectAssistantSidebarProps> = (
  props,
) => featureAvailability.assistant && <AssistantSidebar {...props} />;
