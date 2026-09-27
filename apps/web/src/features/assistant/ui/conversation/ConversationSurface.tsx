import type { AssistantContextValue } from '@/features/assistant/model/assistantContext';
import { Button, IconButton } from '@green/ui';
import { ArrowLeft, MessagesSquare } from 'lucide-react';
import type { RefObject, UIEventHandler } from 'react';
import { type FC } from 'react';
import { AssistantHeader, AssistantSurface } from '../shared/AssistantSurface';
import type { ConversationComposerProps } from './ConversationComposer';
import { ConversationComposer } from './ConversationComposer';
import type { ConversationHistoryProps } from './ConversationHistory';
import { ConversationHistory } from './ConversationHistory';
import type { ConversationListProps } from './ConversationList';
import { ConversationList } from './ConversationList';
interface ConversationSurfaceProps {
  chat: ConversationHistoryProps['chat'] &
    ConversationComposerProps['chat'] &
    ConversationListProps['chat'] &
    Pick<
      AssistantContextValue,
      | 'listOpen'
      | 'conversationTitle'
      | 'closeConversations'
      | 'showConversations'
    >;
  onMap: boolean;
  onAgent: () => void;
  close: () => void;
  history: RefObject<HTMLDivElement | null>;
  composer: RefObject<HTMLTextAreaElement | null>;
  follow: RefObject<boolean>;
  onScroll: UIEventHandler<HTMLDivElement>;
}
export const ConversationSurface: FC<ConversationSurfaceProps> = ({
  chat,
  onMap,
  onAgent,
  close,
  history,
  composer,
  follow,
  onScroll,
}) => {
  const busy = Boolean(chat.pending);
  return (
    <AssistantSurface
      header={
        <AssistantHeader
          title={chat.listOpen ? 'Диалоги проекта' : chat.conversationTitle}
          titleHint={chat.conversationTitle}
          leading={
            <IconButton
              icon={chat.listOpen ? <ArrowLeft /> : <MessagesSquare />}
              variant="ghost"
              label={chat.listOpen ? 'Вернуться в диалог' : 'Диалоги проекта'}
              disabled={busy}
              onClick={() =>
                chat.listOpen
                  ? chat.closeConversations()
                  : void chat.showConversations()
              }
            />
          }
          actions={
            <Button variant="ghost" onClick={onAgent}>
              Агент
            </Button>
          }
          onClose={close}
          closeLabel="Закрыть чат"
        />
      }
      bodyProps={
        chat.listOpen
          ? undefined
          : {
              ref: history,
              role: 'log',
              'aria-label': 'Переписка по проекту',
              'aria-live': 'polite',
              onScroll,
            }
      }
      footer={
        !chat.listOpen ? (
          <ConversationComposer
            chat={chat}
            composer={composer}
            follow={follow}
          />
        ) : undefined
      }
    >
      {chat.listOpen ? (
        <ConversationList chat={chat} />
      ) : (
        <ConversationHistory chat={chat} onMap={onMap} composer={composer} />
      )}
    </AssistantSurface>
  );
};
