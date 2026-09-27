import { AgentActivity } from '@/features/assistant/ui/shared/AgentActivity';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Text } from '@green/ui';
import type { FC } from 'react';
import type { ConversationHistoryProps } from '../ConversationHistory.types';

interface ConversationProposalProps {
  proposal: NonNullable<ConversationHistoryProps['chat']['proposal']>;
  chat: Pick<
    ConversationHistoryProps['chat'],
    'recalculate' | 'projectId' | 'apply' | 'pending' | 'discard'
  >;
  onMap: boolean;
  busy: boolean;
  proposalScope: string;
}
export const ConversationProposal: FC<ConversationProposalProps> = ({
  proposal,
  chat,
  onMap,
  busy,
  proposalScope,
}) => (
  <AssistantCard aria-label="Предложение" tone="neutral">
    <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
      {proposal.stale
        ? 'План изменился'
        : `${proposal.title}${proposal.intent.action === 'delete' ? '?' : ''}`}
    </Text>
    <Text as="p" variant="body" className="m-0 wrap-anywhere">
      {proposal.stale
        ? 'Пересчитайте предложение для текущего плана.'
        : (proposal.scopeLabel ??
          (proposal.intent.scope === 'project'
            ? 'Весь проект'
            : proposal.intent.scope === 'selection'
              ? 'Выбранные посадки'
              : proposalScope))}
    </Text>
    {!!(!proposal.stale && proposal.verificationNotice) && (
      <Text as="p" variant="body" className="m-0 wrap-anywhere">
        {proposal.verificationNotice}
      </Text>
    )}
    {!!(!proposal.stale && proposal.shortfallExplanation) && (
      <Text as="p" variant="body" className="m-0 wrap-anywhere">
        {proposal.shortfallExplanation}
      </Text>
    )}
    {!proposal.stale && !proposal.preview.can_apply && (
      <Text as="p" variant="body" className="m-0 wrap-anywhere">
        Это размещение нельзя применить. Измените запрос.
      </Text>
    )}
    <AgentActivity trace={proposal.agentTrace} />
    <ProposalActions
      proposal={proposal}
      chat={chat}
      onMap={onMap}
      busy={busy}
    />
  </AssistantCard>
);

import { ProposalActions } from './ProposalActions';
