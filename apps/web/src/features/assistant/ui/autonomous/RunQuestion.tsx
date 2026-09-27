import { errorText } from '@/features/assistant/model/autonomous/presentation';
import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
export interface RunQuestionProps extends Pick<
  AutonomousRunController,
  | 'answering'
  | 'run'
  | 'incomplete'
  | 'staleSelection'
  | 'selectionRecovery'
  | 'decisionDisabled'
  | 'remediesForSelection'
  | 'inputDisabled'
  | 'fillDraft'
> {}
export const RunQuestion: FC<RunQuestionProps> = ({
  answering,
  run,
  incomplete,
  staleSelection,
  selectionRecovery,
  decisionDisabled,
  remediesForSelection,
  inputDisabled,
  fillDraft,
}) => (
  <>
    {!!(answering && run?.state.pending_question) && (
      <AssistantCard role="status" tone="info">
        <span className="text-xs font-medium text-neutral-600">
          Нужен ваш ответ
        </span>
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {run.state.pending_question.slot === 'capacity' && incomplete
            ? 'Что изменить в задании? Выберите вариант выше или напишите свой ответ.'
            : run.state.pending_question.question}
        </Text>
        {!!(staleSelection && selectionRecovery.isPending) && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            Обновляем данные проекта для выделения…
          </Text>
        )}
        {!!(staleSelection && selectionRecovery.isError) && (
          <>
            <Text
              role="alert"
              as="p"
              variant="body"
              className="m-0 wrap-anywhere"
            >
              Не удалось обновить данные проекта.{' '}
              {errorText(selectionRecovery.error)}
            </Text>
            <Button
              type="button"
              variant="secondary"
              disabled={decisionDisabled}
              loading={selectionRecovery.isFetching}
              onClick={() => void selectionRecovery.refetch()}
            >
              Обновить данные проекта
            </Button>
          </>
        )}
        {!!(
          run.state.pending_question.slot === 'selection' &&
          remediesForSelection.length
        ) && (
          <div className="flex flex-wrap items-center gap-2 border-t border-neutral-200 pt-3">
            {remediesForSelection.map((remedy) => (
              <Button
                type="button"
                variant="secondary"
                key={remedy.label}
                disabled={inputDisabled}
                onClick={() => fillDraft(remedy.draft)}
              >
                {remedy.label}
              </Button>
            ))}
            <Text as="small" variant="caption" className="m-0 wrap-anywhere">
              Выберите вариант и нажмите «Продолжить», чтобы обновить область
              задания.
            </Text>
          </div>
        )}
      </AssistantCard>
    )}
  </>
);
