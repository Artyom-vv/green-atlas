import type { AssistantContextValue } from '@/features/assistant/model/assistantContext';
import type { RefObject } from 'react';
export interface ConversationHistoryProps {
  chat: Pick<
    AssistantContextValue,
    | 'messages'
    | 'setDraft'
    | 'recalculate'
    | 'projectId'
    | 'apply'
    | 'pending'
    | 'discard'
    | 'pendingIntent'
    | 'project'
    | 'chooseZone'
    | 'undo'
    | 'error'
    | 'retry'
    | 'proposal'
    | 'undoReceipt'
  >;
  onMap: boolean;
  composer: RefObject<HTMLTextAreaElement | null>;
}
