import { Field, FormActions, Text, TextArea, cx } from '@green/ui';
import type { ComponentProps, FC, FormEventHandler, ReactNode } from 'react';

export interface AssistantComposerProps {
  label: ReactNode;
  scope?: ReactNode;
  description?: ReactNode;
  actions: ReactNode;
  input: ComponentProps<typeof TextArea>;
  onSubmit: FormEventHandler<HTMLFormElement>;
}

export const AssistantComposer: FC<AssistantComposerProps> = ({
  label,
  scope,
  description,
  actions,
  input,
  onSubmit,
}) => (
  <form
    className="grid shrink-0 gap-2 border-t border-neutral-200 bg-white p-3"
    onSubmit={onSubmit}
  >
    {scope}
    <Field label={label}>
      <TextArea
        rows={3}
        {...input}
        className={cx('max-h-40 min-h-18 resize-y', input.className)}
      />
    </Field>
    <FormActions>{actions}</FormActions>
    {!!description && <Text variant="caption">{description}</Text>}
  </form>
);
