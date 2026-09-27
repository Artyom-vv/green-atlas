import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Text } from '@green/ui';
import { LoaderCircle } from 'lucide-react';
import type { FC } from 'react';
import type { RunTaskProps } from './RunTask.types';
export interface SubmittedTaskProps extends Pick<
  RunTaskProps,
  'creating' | 'draft' | 'submitted'
> {}
export const SubmittedTask: FC<SubmittedTaskProps> = ({
  creating,
  draft,
  submitted,
}) => (
  <>
    {!!creating && (
      <>
        <AssistantCard tone="neutral">
          <span>Ваша задача</span>
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {draft.trim()}
          </Text>
          {!!submitted?.label && (
            <Text
              aria-label="Выделение при отправке"
              className="text-xs text-neutral-600"
              as="p"
              variant="body"
            >
              При отправке выделено: {submitted.label}.
            </Text>
          )}
        </AssistantCard>
        <AssistantCard
          role="status"
          tone="info"
          className="flex flex-wrap items-start gap-2"
        >
          <LoaderCircle
            size={18}
            aria-hidden="true"
            className="animate-spin motion-reduce:animate-none"
          />
          <div>
            <Text as="strong" variant="label" className="m-0 wrap-anywhere">
              Проверяю формулировку задания
            </Text>
            <Text as="p" variant="body" className="m-0 wrap-anywhere">
              Подготавливаю параметры для расчёта.
            </Text>
          </div>
        </AssistantCard>
      </>
    )}
  </>
);
