import type { FC, Ref } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import { Button, Field, InlineMessage, TextArea } from '@green/ui';
import type { RecommendationFormValues } from '../model/recommendationForm';

export interface TaskEntryProps {
  inputRef: Ref<HTMLTextAreaElement>;
  interpreting: boolean;
  feedback: string[];
  requiresComposition: boolean;
  onChooseComposition?: () => void;
  onManualEntry: () => void;
  onAutomaticEntry: () => void;
}
export const TaskEntry: FC<TaskEntryProps> = ({
  inputRef,
  interpreting,
  feedback,
  requiresComposition,
  onChooseComposition,
  onManualEntry,
  onAutomaticEntry,
}) => {
  const { control } = useFormContext<RecommendationFormValues>();
  const { field } = useController({ name: 'task', control });
  return (
    <div className="grid min-w-0 gap-3">
      <Button
        variant="secondary"
        onClick={onAutomaticEntry}
        disabled={interpreting}
      >
        Автоматически по участкам
      </Button>
      <Field label="Что нужно получить?">
        <TextArea
          {...field}
          ref={(node) => {
            field.ref(node);
            if (typeof inputRef === 'function') inputRef(node);
            else if (inputRef) inputRef.current = node;
          }}
          aria-label="Задача озеленения"
          placeholder="Например: прикрыть здания группами деревьев"
          rows={4}
          maxLength={2000}
          disabled={interpreting}
          className="min-h-32 resize-y"
        />
      </Field>
      {!!feedback.length && (
        <InlineMessage tone="info">
          <div role="status" className="grid gap-2">
            {feedback.map((item, index) => (
              <p className="m-0" key={index}>
                {item}
              </p>
            ))}
          </div>
        </InlineMessage>
      )}
      <Button
        variant="secondary"
        className="justify-self-start"
        onClick={
          requiresComposition && onChooseComposition
            ? onChooseComposition
            : onManualEntry
        }
      >
        {requiresComposition && onChooseComposition
          ? 'Выбрать состав и количество'
          : 'Настроить вручную'}
      </Button>
    </div>
  );
};
