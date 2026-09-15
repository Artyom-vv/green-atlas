import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Check } from 'lucide-react';
import type { FC } from 'react';
import type { RunFeedbackProps } from './RunFeedback.types';
export interface CompletedFeedbackProps extends Pick<
  RunFeedbackProps,
  'run' | 'committed' | 'shownZone' | 'zoneCommitted'
> {}
export const CompletedFeedback: FC<CompletedFeedbackProps> = ({
  run,
  committed,
  shownZone,
  zoneCommitted,
}) => (
  <>
    {run?.state.status === 'finished' && (
      <AssistantCard
        tone={committed ? 'success' : 'muted'}
        className="flex items-start gap-2"
      >
        <Check size={16} aria-hidden="true" />
        <span>
          {shownZone
            ? `На карте показан участок «${shownZone}».`
            : committed
              ? zoneCommitted
                ? 'Изменение участка применено.'
                : 'Изменение применено к плану.'
              : 'Проверка завершена. План не изменён.'}
        </span>
      </AssistantCard>
    )}
  </>
);
