import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import { AgentActivity } from '@/features/assistant/ui/shared/AgentActivity';
import { Button, cx, Text } from '@green/ui';
import { Undo2 } from 'lucide-react';
import type { FC } from 'react';
import type { ConversationHistoryProps } from '../ConversationHistory.types';

import { ConversationProposal } from './ConversationProposal';
import { ConversationZoneQuestion } from './ConversationZoneQuestion';
interface ConversationMessageProps {
  chat: ConversationHistoryProps['chat'];
  line: ConversationHistoryProps['chat']['messages'][number];
  onMap: boolean;
}
export const ConversationMessage: FC<ConversationMessageProps> = ({
  chat,
  line,
  onMap,
}) => {
  const busy = Boolean(chat.pending);

  const proposal = chat.proposal;
  const proposalZone = chat.project?.planting_zones?.find(
    (zone) => zone.id === proposal?.intent.zone_id,
  );
  const proposalScope = proposalZone
    ? repeatedItemLabel(
        chat.project?.planting_zones ?? [],
        proposalZone,
      ).replace(/\s*[·•]\s*/g, ' ')
    : '';
  const receipt = chat.undoReceipt;
  const canUndo =
    receipt &&
    receipt.version === chat.project?.plan?.version &&
    receipt.stateVersion === chat.project?.state_version;
  return (
    <article
      key={line.id}
      className={cx(
        'grid min-w-0 shrink-0 gap-3',
        line.role === 'user' && 'rounded-card bg-neutral-100 p-3',
      )}
      aria-label={line.role === 'user' ? 'Вы' : 'Помощник'}
    >
      {!!line.text && (
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {line.text}
        </Text>
      )}
      {!!line.activity && <AgentActivity trace={line.activity} />}
      {!!(line.result && proposal?.messageId !== line.id) && (
        <Text className="text-xs text-neutral-600" as="p" variant="body">
          {line.result}
        </Text>
      )}
      {proposal?.messageId === line.id && (
        <ConversationProposal
          proposal={proposal}
          chat={chat}
          onMap={onMap}
          busy={busy}
          proposalScope={proposalScope}
        />
      )}
      {chat.pendingIntent?.messageId === line.id && (
        <ConversationZoneQuestion
          key={chat.pendingIntent.messageId}
          chat={chat}
        />
      )}
      {!!(receipt?.messageId === line.id && canUndo) && (
        <Button
          variant="secondary"
          icon={<Undo2 />}
          disabled={busy}
          onClick={() => void chat.undo()}
        >
          Отменить изменение
        </Button>
      )}
    </article>
  );
};
