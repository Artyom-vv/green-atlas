import { useCallback } from 'react';
import { useForm, useWatch } from 'react-hook-form';

export interface AssistantDraft {
  message: string;
}

/** One active draft. Conversation switching explicitly saves/restores its value. */
export function useAssistantDraft() {
  const form = useForm<AssistantDraft>({ defaultValues: { message: '' } });
  const draft = useWatch({ control: form.control, name: 'message' });
  const { setValue, reset } = form;
  const setDraft = useCallback(
    (value: string) => {
      if (value === '') reset({ message: '' });
      else setValue('message', value, { shouldDirty: true });
    },
    [reset, setValue],
  );
  return { form, draft, setDraft };
}
