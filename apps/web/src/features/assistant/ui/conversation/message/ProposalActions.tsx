import { Button } from '@green/ui';
import type { FC } from 'react';
import { useNavigate } from 'react-router-dom';
import type { ConversationHistoryProps } from '../ConversationHistory.types';

interface ProposalActionsProps {
  proposal: NonNullable<ConversationHistoryProps['chat']['proposal']>;
  chat: Pick<
    ConversationHistoryProps['chat'],
    'recalculate' | 'projectId' | 'apply' | 'pending' | 'discard'
  >;
  onMap: boolean;
  busy: boolean;
}
export const ProposalActions: FC<ProposalActionsProps> = ({
  proposal,
  chat,
  onMap,
  busy,
}) => {
  const navigate = useNavigate();
  return (
    <div className="flex flex-wrap gap-2">
      {proposal.stale ? (
        <Button variant="primary" disabled={busy} onClick={chat.recalculate}>
          Пересчитать
        </Button>
      ) : !onMap ? (
        <Button
          variant="primary"
          onClick={() => navigate(`/projects/${chat.projectId}/workspace`)}
        >
          Показать на карте
        </Button>
      ) : (
        <Button
          variant={proposal.intent.action === 'delete' ? 'danger' : 'primary'}
          disabled={busy || !proposal.preview.can_apply}
          onClick={() => void chat.apply()}
        >
          {chat.pending === 'applying'
            ? 'Применяем…'
            : proposal.intent.action === 'delete'
              ? 'Удалить'
              : 'Применить'}
        </Button>
      )}
      <Button variant="secondary" disabled={busy} onClick={chat.discard}>
        {proposal.intent.action === 'delete' ? 'Оставить' : 'Отказаться'}
      </Button>
    </div>
  );
};
