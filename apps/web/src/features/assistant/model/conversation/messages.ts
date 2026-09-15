import type {
  AgentTrace,
  ChatLine,
} from '@/features/assistant/model/assistantContext';
import type { Conversation, LegacyChatImport } from '@green/api-client';
export function legacyMessages(
  raw: string | null,
): LegacyChatImport['messages'] {
  if (!raw) return [];
  const value: unknown = JSON.parse(raw);
  if (!Array.isArray(value) || value.length > 1000)
    throw new Error(
      'Не удалось перенести переписку. Исходная копия сохранена в браузере.',
    );
  return value.map((item: unknown) => {
    if (!item || typeof item !== 'object')
      throw new Error('Повреждена запись переписки');
    const line = item as Record<string, unknown>;
    if (
      typeof line.id !== 'string' ||
      !line.id ||
      line.id.length > 200 ||
      (line.role !== 'user' && line.role !== 'assistant') ||
      typeof line.text !== 'string' ||
      !line.text ||
      line.text.length > 2000 ||
      (line.result != null &&
        (typeof line.result !== 'string' || line.result.length > 2000))
    ) {
      throw new Error(
        'Не удалось перенести запись. Исходная копия сохранена в браузере.',
      );
    }
    return {
      id: line.id,
      role: line.role,
      text: line.text,
      result: line.result as string | null | undefined,
    };
  });
}
export function conversationMessages(conversation: Conversation): ChatLine[] {
  const inspectionTrace = (sourceId: string): AgentTrace | undefined => {
    const record = conversation.records.find(
      (item) =>
        item.record_id === `tools:${sourceId}` && item.kind === 'tool_event',
    );
    const content = record?.payload.content as
      { event?: string; inspection?: { events?: unknown[] } } | undefined;
    if (
      content?.event !== 'project_inspected' ||
      !Array.isArray(content.inspection?.events)
    )
      return undefined;
    const events = content.inspection.events.flatMap((item) => {
      if (!item || typeof item !== 'object') return [];
      const event = item as Record<string, unknown>;
      return [
        {
          ok: event.ok !== false,
          tool: typeof event.tool === 'string' ? event.tool : undefined,
          effect: typeof event.effect === 'string' ? event.effect : undefined,
        },
      ];
    });
    return events.length ? { events } : undefined;
  };
  return conversation.records.flatMap((record) => {
    if (record.kind !== 'message') return [];
    const content = record.payload.content as
      Record<string, unknown> | undefined;
    if (
      !content ||
      (content.role !== 'user' && content.role !== 'assistant') ||
      typeof content.text !== 'string'
    )
      return [];
    const sourceId =
      content.role === 'assistant' && record.record_id.startsWith('answer:')
        ? record.record_id.slice('answer:'.length)
        : '';
    return [
      {
        id: record.record_id,
        role: content.role,
        text: content.text,
        activity: sourceId ? inspectionTrace(sourceId) : undefined,
      },
    ];
  });
}
