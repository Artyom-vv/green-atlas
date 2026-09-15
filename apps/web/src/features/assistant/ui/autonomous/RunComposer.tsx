import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { AssistantComposer } from '@/features/assistant/ui/shared/AssistantComposer';
import { Button, Text } from '@green/ui';
import { ArrowUp } from 'lucide-react';
import type { FC } from 'react';
export interface RunComposerProps extends Pick<
  AutonomousRunController,
  | 'approval'
  | 'submit'
  | 'creating'
  | 'currentSelection'
  | 'submitted'
  | 'busy'
  | 'availableSelectionLabel'
  | 'answering'
  | 'inputRef'
  | 'draft'
  | 'setDraft'
  | 'inputDisabled'
  | 'restoring'
  | 'lifecycle'
  | 'active'
> {}
export const RunComposer: FC<RunComposerProps> = ({
  approval,
  submit,
  creating,
  currentSelection,
  submitted,
  busy,
  availableSelectionLabel,
  answering,
  inputRef,
  draft,
  setDraft,
  inputDisabled,
  restoring,
  lifecycle,
  active,
}) => (
  <>
    {!approval && (
      <AssistantComposer
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        label={answering ? 'Ответ агенту' : 'Задача для автономного агента'}
        scope={
          <>
            {!!(!creating && (currentSelection || submitted)) && (
              <Text
                aria-label="Выделение на карте"
                aria-live="polite"
                className="text-xs text-neutral-600"
                as="p"
                variant="body"
              >
                {busy && submitted
                  ? submitted.label
                    ? `При отправке выделено: ${submitted.label}.`
                    : 'Запрос отправлен без выделения.'
                  : availableSelectionLabel
                    ? `Выделено на карте: ${availableSelectionLabel}.`
                    : 'На карте нет выделения.'}
              </Text>
            )}
          </>
        }
        input={{
          id: 'autonomous-agent-input',
          ref: inputRef,
          placeholder: answering
            ? 'Уточните условия или выберите вариант выше'
            : 'Что нужно сделать на плане?',
          value: draft,
          onChange: (event) => setDraft(event.target.value),
          maxLength: answering ? 800 : 2000,
          minLength: answering ? 1 : 5,
          rows: 3,
          disabled: inputDisabled,
        }}
        actions={
          <Button
            type="submit"
            variant="primary"
            icon={<ArrowUp />}
            loading={busy || restoring}
            disabled={
              draft.trim().length < (answering ? 1 : 5) || inputDisabled
            }
          >
            {lifecycle.canContinue
              ? 'Ожидает продолжения'
              : active
                ? 'Выполняется'
                : answering
                  ? 'Продолжить'
                  : 'Поставить задачу'}
          </Button>
        }
        description="Изменения применяются только после подтверждения."
      />
    )}
  </>
);
