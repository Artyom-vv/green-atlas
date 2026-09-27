import type { AssistantContextValue } from '@/features/assistant/model/assistantContext';
import { AssistantComposer } from '@/features/assistant/ui/shared/AssistantComposer';
import { IconButton } from '@green/ui';
import { ArrowUp, Square } from 'lucide-react';
import type { FC, RefObject } from 'react';
export interface ConversationComposerProps {
  chat: Pick<
    AssistantContextValue,
    | 'send'
    | 'selectedIds'
    | 'selectedZoneIds'
    | 'draft'
    | 'setDraft'
    | 'stop'
    | 'project'
    | 'pending'
  >;
  composer: RefObject<HTMLTextAreaElement | null>;
  follow: RefObject<boolean>;
}
export const ConversationComposer: FC<ConversationComposerProps> = ({
  chat,
  composer,
  follow,
}) => {
  const busy = Boolean(chat.pending);
  const openingConversation = chat.pending === 'loading';
  const stoppable = chat.pending === 'thinking' || chat.pending === 'preparing';
  return (
    <AssistantComposer
      label="Сообщение помощнику"
      scope={
        <span className="text-xs text-neutral-600">
          {chat.selectedIds.length && chat.selectedZoneIds.length
            ? 'Выбраны участки и посадки'
            : chat.selectedIds.length
              ? `Выбрано посадок: ${chat.selectedIds.length}`
              : chat.selectedZoneIds.length
                ? `Выбрано участков: ${chat.selectedZoneIds.length}`
                : 'Проект без выделения'}
        </span>
      }
      onSubmit={(event) => {
        event.preventDefault();
        follow.current = true;
        void chat.send();
      }}
      input={{
        ref: composer,
        'aria-label': 'Сообщение помощнику',
        placeholder: 'Что нужно сделать?',
        value: chat.draft,
        disabled: openingConversation,
        maxLength: 2000,
        rows: 3,
        onChange: (event) => chat.setDraft(event.target.value),
        onKeyDown: (event) => {
          if (
            event.key === 'Enter' &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault();
            if (!busy) {
              follow.current = true;
              void chat.send();
            }
          }
        },
      }}
      actions={
        stoppable ? (
          <IconButton
            icon={<Square />}
            variant="secondary"
            label="Остановить запрос"
            onClick={chat.stop}
          />
        ) : (
          <IconButton
            icon={<ArrowUp />}
            variant="primary"
            label="Отправить сообщение"
            type="submit"
            disabled={busy || !chat.draft.trim() || !chat.project}
          />
        )
      }
    />
  );
};
