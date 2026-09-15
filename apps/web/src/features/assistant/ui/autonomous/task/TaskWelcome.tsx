import { starters } from '@/features/assistant/model/autonomous/presentation';
import { Button, Text } from '@green/ui';
import { ArrowUp, Sparkles } from 'lucide-react';
import type { FC } from 'react';
import type { RunTaskProps } from './RunTask.types';
export interface TaskWelcomeProps extends Pick<
  RunTaskProps,
  | 'run'
  | 'restoring'
  | 'failedRestore'
  | 'creating'
  | 'fillDraft'
  | 'decisionDisabled'
> {}
export const TaskWelcome: FC<TaskWelcomeProps> = ({
  run,
  restoring,
  failedRestore,
  creating,
  fillDraft,
  decisionDisabled,
}) => (
  <>
    {!run && !restoring && !failedRestore && !creating && (
      <div className="grid gap-3 py-4">
        <span className="rounded-card grid size-10 place-items-center bg-blue-100 text-blue-600">
          <Sparkles size={23} aria-hidden="true" />
        </span>
        <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
          От задачи — к предложению
        </Text>
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Опишите результат. Агент проверит участки и подготовит вариант для
          вашего решения.
        </Text>
        <div className="flex flex-wrap gap-2">
          {starters.map((starter) => (
            <Button
              type="button"
              key={starter.label}
              variant="secondary"
              endIcon={<ArrowUp />}
              onClick={() => fillDraft(starter.text)}
              disabled={decisionDisabled}
            >
              {starter.label}
            </Button>
          ))}
        </div>
      </div>
    )}
  </>
);
