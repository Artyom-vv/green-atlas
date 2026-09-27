import { eventPresentation } from '@/features/assistant/model/autonomous/presentation';
import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { ActivityTimeline } from '@/features/assistant/ui/shared/ActivityTimeline';
import type { FC } from 'react';
export interface RunActivityProps {
  events: AutonomousRunController['events'];
  incomplete: AutonomousRunController['incomplete'];
}
export const RunActivity: FC<RunActivityProps> = ({ events, incomplete }) => (
  <ActivityTimeline
    items={events.map((event) => {
      const item = eventPresentation(event, incomplete);
      return {
        id: event.sequence + '-' + event.kind,
        title: item.title,
        detail: item.detail,
        tone:
          item.tone === 'done'
            ? 'success'
            : item.tone === 'partial'
              ? 'warning'
              : item.tone === 'error'
                ? 'error'
                : 'neutral',
      };
    })}
  />
);
