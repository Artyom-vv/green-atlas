import { useProjectAssistant } from '@/features/assistant/model/assistantContext';
import { featureAvailability } from '@/shared/config/featureAvailability';
import { Button } from '@green/ui';
import { MessageSquare } from 'lucide-react';
import { type FC } from 'react';
export interface ProjectAssistantTriggerProps {
  disabled?: boolean;
  onOpen?: () => void;
}
const AssistantTrigger: FC<ProjectAssistantTriggerProps> = ({
  disabled = false,
  onOpen,
}) => {
  const chat = useProjectAssistant();
  return (
    <Button
      data-assistant-trigger
      icon={<MessageSquare />}
      variant={chat.open ? 'primary' : 'secondary'}
      disabled={disabled}
      aria-expanded={chat.open}
      aria-controls="project-assistant"
      onClick={() => {
        if (!chat.open) onOpen?.();
        chat.setOpen(!chat.open);
      }}
    >
      Помощник
    </Button>
  );
};

export const ProjectAssistantTrigger: FC<ProjectAssistantTriggerProps> = (
  props,
) => featureAvailability.assistant && <AssistantTrigger {...props} />;
