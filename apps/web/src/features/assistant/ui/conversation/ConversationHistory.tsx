import { AgentActivity } from '@/features/assistant/ui/shared/AgentActivity';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
import type { ConversationHistoryProps } from './ConversationHistory.types';
import { ConversationGreeting } from './message/ConversationGreeting';
import { ConversationMessage } from './message/ConversationMessage';
export type { ConversationHistoryProps } from './ConversationHistory.types';
export const ConversationHistory: FC<ConversationHistoryProps> = ({
  chat,
  onMap,
  composer,
}) => {
  const busy = Boolean(chat.pending);
  const proposal = chat.proposal;
  return (
    <>
      {!chat.messages.length && (
        <ConversationGreeting
          setDraft={chat.setDraft}
          composer={composer}
          disabled={chat.pending === 'loading'}
        />
      )}

      {chat.messages.map((line) => (
        <ConversationMessage
          key={line.id}
          line={line}
          chat={chat}
          onMap={onMap}
        />
      ))}

      {!!chat.pending && <AgentActivity pending={chat.pending} />}

      {!!chat.error && (
        <div role="alert" className="text-error grid gap-2">
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {chat.error}
          </Text>
          <Button
            variant="secondary"
            disabled={busy || Boolean(proposal?.stale)}
            onClick={chat.retry}
          >
            Повторить
          </Button>
        </div>
      )}
    </>
  );
};
