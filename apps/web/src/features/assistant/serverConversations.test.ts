import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Conversation } from '@green/api-client';
import { conversationMessages, legacyMessages, openProjectConversation } from './serverConversations';

const chat: Conversation = { id: 'server-chat', project_id: 'p', title: 'Диалог', revision: 1, can_prepare: false,
  created_at: '2026-09-06', updated_at: '2026-09-06', task: { values: {}, provenance: {} }, records: [] };
const client = () => ({ listConversations: vi.fn().mockResolvedValue([]), getConversation: vi.fn().mockResolvedValue(chat),
  createConversation: vi.fn().mockResolvedValue(chat), importConversation: vi.fn().mockResolvedValue(chat) });
beforeEach(() => { localStorage.clear(); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => vi.unstubAllGlobals());

describe('server conversation migration', () => {
  it('imports original text and status repeatably without deleting local history', async () => {
    const raw = JSON.stringify([{ id: 'one', role: 'assistant', text: 'Посадки вдоль зданий', result: 'Предложение отклонено' }]);
    localStorage.setItem('green-project-chat-v1:p', raw);
    const api = client();
    await openProjectConversation('p', localStorage, api);
    await openProjectConversation('p', localStorage, api);
    expect(api.importConversation.mock.calls[0]).toEqual(api.importConversation.mock.calls[1]);
    expect(api.importConversation.mock.calls[0][1].messages[0]).toEqual(JSON.parse(raw)[0]);
    expect(api.createConversation).not.toHaveBeenCalled();
    expect(localStorage.getItem('green-project-chat-v1:p')).toBe(raw);
  });
  it('reuses the creation ID after a lost response', async () => {
    const api = client();
    api.createConversation.mockRejectedValueOnce(new Error('lost response'));
    await expect(openProjectConversation('p', localStorage, api)).rejects.toThrow('lost response');
    await openProjectConversation('p', localStorage, api);
    expect(api.createConversation.mock.calls[0][2]).toBeTruthy();
    expect(api.createConversation.mock.calls[1][2]).toBe(api.createConversation.mock.calls[0][2]);
  });
  it('restores the chosen conversation only within this project', async () => {
    localStorage.setItem('green-active-conversation:p', 'other-project-chat');
    const api = client();
    api.listConversations.mockResolvedValue([{ ...chat, id: 'this-project-chat' }]);
    await openProjectConversation('p', localStorage, api);
    expect(api.getConversation).toHaveBeenCalledWith('p', 'this-project-chat');
    expect(api.createConversation).not.toHaveBeenCalled();
  });
  it('does not silently truncate or filter invalid history', async () => {
    const raw = JSON.stringify([{ id: 'valid', role: 'user', text: 'Вдоль зданий' }, { id: 'broken', role: 'assistant' }]);
    localStorage.setItem('green-project-chat-v1:p', raw);
    const api = client();
    await expect(openProjectConversation('p', localStorage, api)).rejects.toThrow();
    expect(api.importConversation).not.toHaveBeenCalled();
    expect(localStorage.getItem('green-project-chat-v1:p')).toBe(raw);
    expect(legacyMessages(JSON.stringify(Array.from({ length: 150 }, (_, i) => ({ id: String(i), role: 'user', text: 'Запись' }))))).toHaveLength(150);
  });
  it('does not replace messages with tool events in the rendered history', () => {
    const records: Conversation['records'] = [
      { record_id: 'answer', sequence: 2, kind: 'message', created_at: '', payload: { content: { role: 'assistant', text: 'Исходный ответ' } } },
      { record_id: 'status', sequence: 3, kind: 'tool_event', created_at: '', payload: { content: { status: 'declined' } } },
    ];
    expect(conversationMessages({ ...chat, records })).toEqual([{ id: 'answer', role: 'assistant', text: 'Исходный ответ' }]);
  });
  it('attaches safe inspection activity to the assistant answer', () => {
    const records: Conversation['records'] = [
      { record_id: 'question', sequence: 2, kind: 'message', created_at: '', payload: { content: { role: 'user', text: 'Покажи рост' } } },
      { record_id: 'tools:question', sequence: 3, kind: 'tool_event', created_at: '', payload: { content: {
        event: 'project_inspected', inspection: { events: [{ ok: true, tool: 'growth_scene', arguments: { secret: 'hidden' } }] },
      } } },
      { record_id: 'answer:question', sequence: 4, kind: 'message', created_at: '', payload: { content: { role: 'assistant', text: 'Рост рассчитан' } } },
    ];
    const answer = conversationMessages({ ...chat, records })[1];
    expect(answer.activity).toEqual({ events: [{ ok: true, tool: 'growth_scene', effect: undefined }] });
    expect(JSON.stringify(answer)).not.toContain('hidden');
  });
});
