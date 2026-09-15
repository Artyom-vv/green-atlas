import type { AgentTrace } from '@/features/assistant/model/assistantContext';
import {
  eventCopy,
  pendingLabel,
  type PendingState,
} from '@/features/assistant/model/conversation/activityPresentation';
import { Icon, Text } from '@green/ui';
import { LoaderCircle } from 'lucide-react';
import type { FC } from 'react';
import { ActivityTimeline } from './ActivityTimeline';
export interface AgentActivityProps {
  trace?: AgentTrace;
  pending?: PendingState;
}
export const AgentActivity: FC<AgentActivityProps> = ({ trace, pending }) => {
  const events = (trace?.events ?? []).filter(
    (event) =>
      event && typeof event === 'object' && (event.tool || event.ok === false),
  );
  if (pending)
    return (
      <div
        className="flex min-h-8 items-center gap-2 text-neutral-600"
        role="status"
        aria-live="polite"
      >
        <Icon
          icon={<LoaderCircle />}
          className="animate-spin motion-reduce:animate-none"
        />
        <Text variant="caption">{pendingLabel(pending)}</Text>
      </div>
    );
  return (
    <ActivityTimeline
      items={events.map((event, index) => {
        const copy = eventCopy(event);
        return {
          id: (event.tool ?? 'step') + '-' + index,
          title: copy.title,
          detail: copy.detail,
          tone: copy.state === 'done' ? 'success' : 'warning',
        };
      })}
    />
  );
};
