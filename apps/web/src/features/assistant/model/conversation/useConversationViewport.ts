import { useEffect, useRef, type UIEventHandler } from 'react';
import type { AssistantContextValue } from '../assistantContext';
const FOLLOW_SCROLL_THRESHOLD = 64;
type ConversationViewportOptions = Pick<
  AssistantContextValue,
  | 'pending'
  | 'open'
  | 'listOpen'
  | 'activeConversationId'
  | 'messages'
  | 'proposal'
  | 'pendingIntent'
  | 'error'
>;
export function useConversationViewport(chat: ConversationViewportOptions) {
  const history = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const openingConversation = chat.pending === 'loading';
  useEffect(() => {
    if (!chat.open || openingConversation) return;
    composer.current?.focus();
    if (follow.current && history.current)
      history.current.scrollTop = history.current.scrollHeight;
  }, [
    chat.open,
    chat.listOpen,
    chat.activeConversationId,
    openingConversation,
  ]);
  useEffect(() => {
    if (follow.current && history.current)
      history.current.scrollTop = history.current.scrollHeight;
  }, [
    chat.messages,
    chat.pending,
    chat.proposal,
    chat.pendingIntent,
    chat.error,
  ]);
  const onScroll: UIEventHandler<HTMLDivElement> = (event) => {
    const el = event.currentTarget;
    follow.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <
      FOLLOW_SCROLL_THRESHOLD;
  };
  return { history, composer, follow, onScroll };
}
