import { legacyMessages } from '@/features/assistant/model/conversation/messages';
import {
  api,
  type Conversation,
  type LegacyChatImport,
} from '@green/api-client';

type ConversationApi = Pick<
  typeof api,
  | 'listConversations'
  | 'getConversation'
  | 'createConversation'
  | 'importConversation'
>;
type BrowserStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** Import the original text and status separately. Never truncate or erase it. */

async function historyIdentity(messages: LegacyChatImport['messages']) {
  const bytes = new TextEncoder().encode(JSON.stringify(messages));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return `browser-v1-${Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
}

/** Resolve one project session; retries cannot create duplicate chats. */
export async function openProjectConversation(
  projectId: string,
  storage: BrowserStorage = localStorage,
  client: ConversationApi = api,
): Promise<Conversation> {
  const messages = legacyMessages(
    storage.getItem(`green-project-chat-v1:${projectId}`),
  );
  let imported: Conversation | undefined;
  if (messages.length) {
    imported = await client.importConversation(projectId, {
      source_id: await historyIdentity(messages),
      messages,
    });
  }
  const chats = await client.listConversations(projectId);
  const activeKey = `green-active-conversation:${projectId}`;
  const savedId = storage.getItem(activeKey);
  const chosen =
    chats.find((chat) => chat.id === savedId)?.id ??
    imported?.id ??
    chats[0]?.id;
  let conversation: Conversation;
  if (chosen) {
    conversation =
      imported?.id === chosen
        ? imported
        : await client.getConversation(projectId, chosen);
  } else {
    const requestKey = `green-initial-conversation-request:${projectId}`;
    let requestId = storage.getItem(requestKey);
    if (!requestId) {
      requestId = crypto.randomUUID();
      // Persist before sending: a lost response or remount reuses the same ID.
      storage.setItem(requestKey, requestId);
    }
    conversation = await client.createConversation(
      projectId,
      'Новый диалог',
      requestId,
    );
  }
  storage.setItem(activeKey, conversation.id);
  return conversation;
}
