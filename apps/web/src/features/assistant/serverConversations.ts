import { api, type Conversation, type LegacyChatImport } from '@green/api-client';
import type { AgentTrace, ChatLine } from './assistantContext';

type ConversationApi = Pick<typeof api, 'listConversations' | 'getConversation' | 'createConversation' | 'importConversation'>;
type BrowserStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** Import the original text and status separately. Never truncate or erase it. */
export function legacyMessages(raw: string | null): LegacyChatImport['messages'] {
  if (!raw) return [];
  const value: unknown = JSON.parse(raw);
  if (!Array.isArray(value) || value.length > 1000) throw new Error('Не удалось перенести переписку. Исходная копия сохранена в браузере.');
  return value.map((item: unknown) => {
    if (!item || typeof item !== 'object') throw new Error('Повреждена запись переписки');
    const line = item as Record<string, unknown>;
    if (typeof line.id !== 'string' || !line.id || line.id.length > 200 ||
      (line.role !== 'user' && line.role !== 'assistant') || typeof line.text !== 'string' ||
      !line.text || line.text.length > 2000 ||
      (line.result != null && (typeof line.result !== 'string' || line.result.length > 2000))) {
      throw new Error('Не удалось перенести запись. Исходная копия сохранена в браузере.');
    }
    return { id: line.id, role: line.role, text: line.text, result: line.result as string | null | undefined };
  });
}

async function historyIdentity(messages: LegacyChatImport['messages']) {
  const bytes = new TextEncoder().encode(JSON.stringify(messages));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return `browser-v1-${Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')}`;
}

/** Resolve one project session; retries cannot create duplicate chats. */
export async function openProjectConversation(projectId: string, storage: BrowserStorage = localStorage, client: ConversationApi = api): Promise<Conversation> {
  const messages = legacyMessages(storage.getItem(`green-project-chat-v1:${projectId}`));
  let imported: Conversation | undefined;
  if (messages.length) {
    imported = await client.importConversation(projectId, { source_id: await historyIdentity(messages), messages });
  }
  const chats = await client.listConversations(projectId);
  const activeKey = `green-active-conversation:${projectId}`;
  const savedId = storage.getItem(activeKey);
  const chosen = chats.find(chat => chat.id === savedId)?.id ?? imported?.id ?? chats[0]?.id;
  let conversation: Conversation;
  if (chosen) {
    conversation = imported?.id === chosen ? imported : await client.getConversation(projectId, chosen);
  } else {
    const requestKey = `green-initial-conversation-request:${projectId}`;
    let requestId = storage.getItem(requestKey);
    if (!requestId) {
      requestId = crypto.randomUUID();
      // Persist before sending: a lost response or remount reuses the same ID.
      storage.setItem(requestKey, requestId);
    }
    conversation = await client.createConversation(projectId, 'Новый диалог', requestId);
  }
  storage.setItem(activeKey, conversation.id);
  return conversation;
}

export function conversationMessages(conversation: Conversation): ChatLine[] {
  const inspectionTrace = (sourceId: string): AgentTrace | undefined => {
    const record = conversation.records.find(item => item.record_id === `tools:${sourceId}` && item.kind === 'tool_event');
    const content = record?.payload.content as { event?: string; inspection?: { events?: unknown[] } } | undefined;
    if (content?.event !== 'project_inspected' || !Array.isArray(content.inspection?.events)) return undefined;
    const events = content.inspection.events.flatMap(item => {
      if (!item || typeof item !== 'object') return [];
      const event = item as Record<string, unknown>;
      return [{ ok: event.ok !== false, tool: typeof event.tool === 'string' ? event.tool : undefined, effect: typeof event.effect === 'string' ? event.effect : undefined }];
    });
    return events.length ? { events } : undefined;
  };
  return conversation.records.flatMap(record => {
    if (record.kind !== 'message') return [];
    const content = record.payload.content as Record<string, unknown> | undefined;
    if (!content || (content.role !== 'user' && content.role !== 'assistant') || typeof content.text !== 'string') return [];
    const sourceId = content.role === 'assistant' && record.record_id.startsWith('answer:') ? record.record_id.slice('answer:'.length) : '';
    return [{ id: record.record_id, role: content.role, text: content.text, activity: sourceId ? inspectionTrace(sourceId) : undefined }];
  });
}
