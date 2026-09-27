import type { AssistantContextValue } from '@/features/assistant/model/assistantContext';
import { AssistantRecordButton } from '@/features/assistant/ui/shared/AssistantRecordButton';
import { Button, Text, TextInput } from '@green/ui';
import { ChevronRight, Plus, Search } from 'lucide-react';
import type { FC } from 'react';
import { useState } from 'react';

export interface ConversationListProps {
  chat: Pick<
    AssistantContextValue,
    | 'newConversation'
    | 'pending'
    | 'error'
    | 'showConversations'
    | 'conversations'
    | 'activeConversationId'
    | 'selectConversation'
  >;
}

export const ConversationList: FC<ConversationListProps> = ({ chat }) => {
  const busy = Boolean(chat.pending);
  const [search, setSearch] = useState('');
  const query = search.trim().toLocaleLowerCase('ru');
  const matching = chat.conversations.filter((item) =>
    item.title.toLocaleLowerCase('ru').includes(query),
  );

  return (
    <>
      <div className="grid gap-2">
        <TextInput
          startIcon={<Search />}
          aria-label="Найти диалог"
          placeholder="Найти диалог"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Button
          icon={<Plus />}
          variant="primary"
          disabled={busy}
          onClick={() => void chat.newConversation()}
        >
          Новый диалог
        </Button>
      </div>
      <nav aria-label="Диалоги проекта" className="flex min-w-0 flex-col gap-1">
        {busy && (
          <Text
            role="status"
            as="p"
            variant="body"
            className="m-0 wrap-anywhere"
          >
            Загрузка диалогов…
          </Text>
        )}
        {!!chat.error && (
          <div role="alert" className="text-error grid gap-2">
            <Text as="p" variant="body" className="m-0 wrap-anywhere">
              {chat.error}
            </Text>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => void chat.showConversations()}
            >
              Обновить список
            </Button>
          </div>
        )}
        {matching.map((item) => (
          <AssistantRecordButton
            type="button"
            key={item.id}
            aria-current={
              item.id === chat.activeConversationId ? 'page' : undefined
            }
            disabled={busy}
            onClick={() => void chat.selectConversation(item.id)}
            title={item.title}
            description={
              Number.isNaN(Date.parse(item.updated_at))
                ? 'Диалог проекта'
                : new Date(item.updated_at).toLocaleString('ru-RU', {
                    day: 'numeric',
                    month: 'long',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
            }
            endIcon={<ChevronRight />}
          />
        ))}
        {!busy && !chat.error && !matching.length && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {query ? 'Диалоги не найдены' : 'Пока нет диалогов'}
          </Text>
        )}
      </nav>
    </>
  );
};
