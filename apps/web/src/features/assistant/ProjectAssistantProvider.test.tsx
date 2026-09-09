import { act, fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type Project, type Conversation, type ChangeSetPreview, type ConversationProposalStatus } from '@green/api-client';
import { ProjectAssistantProvider } from './ProjectAssistantProvider';
import { useProjectAssistant, type AssistantContextValue } from './assistantContext';
import { ProjectAssistantSidebar, ProjectAssistantTrigger } from './ProjectAssistantSidebar';

const project = { id: 'test', name: 'Проверка', state_version: 3, planting_zones: [{ id: 'west', label: 'Западный' }], plan: { version: 2, objects: [{ id: 'tree', kind: 'tree', planting_zone_id: 'west', x: 10, y: 20 }] } } as Project;
const preview: ChangeSetPreview = { id: 'preview', digest: 'digest', base_plan_version: 2, source: 'manual', label: 'Удалить', can_apply: true, additions: [], updates: [], deletion_ids: ['tree'], expires_at: '2099-01-01T00:00:00Z' };
let chat: AssistantContextValue;
let saved: Conversation;
let status: ConversationProposalStatus['status'];
function Harness() { chat = useProjectAssistant(); return <><ProjectAssistantTrigger /><ProjectAssistantSidebar /></>; }
async function setup() {
  saved = { id: 'conversation', project_id: 'test', title: 'Диалог', created_at: '', updated_at: '', revision: 1, records: [], task: { values: {}, provenance: {} }, can_prepare: false };
  status = 'ready';
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
  vi.spyOn(api, 'listConversations').mockResolvedValue([]);
  vi.spyOn(api, 'createConversation').mockImplementation(async () => saved);
  vi.spyOn(api, 'getConversation').mockImplementation(async () => saved);
  vi.spyOn(api, 'getConversationProposalStatus').mockImplementation(async (_, __, record_id) => ({ record_id, status, can_apply: status === 'ready' }));
  vi.spyOn(api, 'interpretConversationMessage').mockImplementation(async (_, __, input) => {
    saved = { ...saved, revision: 3, can_prepare: true, task: { values: { operation: 'delete', scope: 'objects', object_ids: ['tree'] }, provenance: {} },
      records: [{ record_id: input.record_id, kind: 'message', sequence: 2, created_at: '', payload: { content: { role: 'user', text: input.text }, task_patch: { operation: 'delete' } } },
        { record_id: 'answer', kind: 'message', sequence: 3, created_at: '', payload: { content: { role: 'assistant', text: 'Задание сохранено' } } }] };
    return saved;
  });
  vi.spyOn(api, 'prepareConversationTask').mockImplementation(async (_, __, input) => {
    saved = { ...saved, revision: 4, records: [...saved.records, { record_id: input.record_id, kind: 'tool_event', sequence: 4, created_at: '',
      payload: { content: { event: 'task_prepared', preparation: { task: saved.task, events: [{ state_version: 3 }], change_set: preview } } } }] };
    return saved;
  });
  vi.spyOn(api, 'declineConversationProposal').mockImplementation(async () => { status = 'declined'; return saved; });
  vi.spyOn(api, 'applyPlanChanges');
  vi.spyOn(api, 'confirmConversationProposal').mockImplementation(async () => { status = 'applied'; return { plan_version: 3, state_version: 4, deleted_ids: ['tree'] } as Awaited<ReturnType<typeof api.confirmConversationProposal>>; });
  vi.spyOn(api, 'projectChat');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/projects/test/workspace']}><ProjectAssistantProvider><Harness /></ProjectAssistantProvider></MemoryRouter></QueryClientProvider>);
  await waitFor(() => expect(chat.project).toBeDefined());
  fireEvent.click(screen.getByRole('button', { name: 'Помощник' }));
  await waitFor(() => expect(chat.pending).toBeUndefined());
}
beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('server project assistant', () => {
  it('shows partial verification before confirmation instead of implying full compliance', async () => {
    await setup();
    const prepare = vi.mocked(api.prepareConversationTask).getMockImplementation()!;
    vi.mocked(api.prepareConversationTask).mockImplementation(async (...args) => {
      const response = await prepare(...args);
      const record = response.records.find(item => item.kind === 'tool_event')!;
      const content = record.payload.content as { preparation: Record<string, unknown> };
      content.preparation.condition_check = { coverage: 'partial', full_compliance_verified: false };
      return response;
    });
    await act(async () => { await chat.send('Посадите с нормативными отступами'); });
    expect(screen.getByText('Учтены доступные контуры зданий и дорог. Полное соответствие нормам не подтверждено.')).toBeVisible();
    expect(api.confirmConversationProposal).not.toHaveBeenCalled();
  });
  it('switches independent conversations, restores drafts and remembers the active chat', async () => {
    await setup();
    const first = saved;
    const second = { ...saved, id: 'second', title: 'Другой диалог', records: [], task: { values: {}, provenance: {} } };
    vi.mocked(api.getConversation).mockImplementation(async (_, id) => id === 'second' ? second : first);
    vi.mocked(api.listConversations).mockResolvedValue([first, second]);
    act(() => chat.setDraft('Черновик первого'));
    await act(async () => { await chat.showConversations(); });
    expect(screen.getByRole('button', { name: /Другой диалог/ })).toBeVisible();
    await act(async () => { await chat.selectConversation('second'); });
    expect(chat.draft).toBe('');
    expect(chat.activeConversationId).toBe('second');
    expect(chat.proposal).toBeUndefined();
    expect(localStorage.getItem('green-active-conversation:test')).toBe('second');
    act(() => chat.setDraft('Черновик второго'));
    await act(async () => { await chat.selectConversation('conversation'); });
    expect(chat.draft).toBe('Черновик первого');
    await act(async () => { await chat.selectConversation('second'); });
    expect(chat.draft).toBe('Черновик второго');
    expect(api.interpretConversationMessage).not.toHaveBeenCalled();
  });
  it('reuses creation identity after a lost response and clears the previous task UI', async () => {
    await setup();
    await act(async () => { await chat.send('Удалить'); });
    const next = { ...saved, id: 'new', title: 'Новый диалог', records: [], task: { values: {}, provenance: {} }, can_prepare: false };
    vi.mocked(api.createConversation).mockRejectedValueOnce(new Error('Соединение прервано')).mockResolvedValueOnce(next);
    await act(async () => { await chat.newConversation(); });
    expect(chat.activeConversationId).toBe('conversation');
    const requestId = vi.mocked(api.createConversation).mock.calls.at(-1)?.[2];
    await act(async () => { await chat.newConversation(); });
    expect(vi.mocked(api.createConversation).mock.calls.at(-1)?.[2]).toBe(requestId);
    expect(chat.activeConversationId).toBe('new');
    expect(chat.messages).toEqual([]);
    expect(chat.proposal).toBeUndefined();
    expect(chat.undoReceipt).toBeUndefined();
    await act(async () => { chat.retry(); });
    expect(api.interpretConversationMessage).toHaveBeenCalledTimes(1);
  });
  it('keeps an old proposal status before subsequent unrelated answers', async () => {
    await setup();
    await act(async () => { await chat.send('Удалить'); await chat.apply(); });
    status = 'undone';
    vi.mocked(api.interpretConversationMessage).mockImplementation(async (_, __, input) => {
      saved = { ...saved, revision: 6, can_prepare: false, records: [...saved.records,
        { record_id: input.record_id, kind: 'message', sequence: 5, created_at: '', payload: { content: { role: 'user', text: input.text }, task_patch: {} } },
        { record_id: 'count-answer', kind: 'message', sequence: 6, created_at: '', payload: { content: { role: 'assistant', text: 'Всего 239 посадок.' } } }] };
      return saved;
    });
    await act(async () => { await chat.send('Сколько посадок?'); });
    const lines = chat.messages.map(line => line.text);
    expect(lines.indexOf('Изменение отменено.')).toBeLessThan(lines.indexOf('Сколько посадок?'));
    expect(lines.at(-1)).toBe('Всего 239 посадок.');
  });
  it('interprets then prepares through the server and requires confirmation', async () => {
    await setup();
    await act(async () => { await chat.send('Удалите выбранное'); });
    expect(api.projectChat).not.toHaveBeenCalled();
    expect(api.prepareConversationTask).toHaveBeenCalledTimes(1);
    expect(api.applyPlanChanges).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: 'Удалить посадки: 1?' })).toBeVisible();
    await act(async () => { await chat.apply(); });
    expect(api.applyPlanChanges).not.toHaveBeenCalled();
    expect(api.confirmConversationProposal).toHaveBeenCalledWith('test', 'conversation', expect.any(String), { expected_revision: 4 });
    expect(screen.getByText('Изменение применено.')).toBeVisible();
  });
  it('renders the prepared agent trace as a collapsed activity disclosure', async () => {
    await setup();
    const prepare = vi.mocked(api.prepareConversationTask).getMockImplementation()!;
    vi.mocked(api.prepareConversationTask).mockImplementation(async (...args) => {
      const response = await prepare(...args);
      const record = response.records.find(item => item.kind === 'tool_event')!;
      const content = record.payload.content as { preparation: Record<string, unknown> };
      content.preparation.agent_trace = { events: [
        { ok: true, tool: 'project_context' },
        { ok: true, tool: 'prepare_placement', summary: { can_apply: true, found: 1 } },
      ] };
      return response;
    });
    await act(async () => { await chat.send('Удалить'); });
    expect(screen.getByText('Ход работы')).toBeVisible();
    expect(screen.queryByText('Понял проект')).not.toBeVisible();
    await act(async () => { fireEvent.click(screen.getByText('Ход работы')); });
    expect(screen.getByText('Понял проект')).toBeVisible();
    expect(screen.getByText('Вариант проходит проверки (1 место)')).toBeVisible();
  });
  it('requests map focus only after confirmed additions', async () => {
    await setup();
    const placementPreview = { ...preview, source: 'system', additions: [{ id: 'new-tree', kind: 'tree', planting_zone_id: 'west', x: 12, y: 18, radius: 1, size_class: 'standard', spacing_policy: 'balanced', locked: false, status: 'valid' }], deletion_ids: [] } as ChangeSetPreview;
    vi.mocked(api.interpretConversationMessage).mockImplementation(async (_, __, input) => {
      saved = { ...saved, revision: 3, can_prepare: true,
        task: { values: { operation: 'place', scope: 'zones', zone_ids: ['west'], post_action: 'focus_map' }, provenance: {} },
        records: [{ record_id: input.record_id, kind: 'message', sequence: 2, created_at: '', payload: { content: { role: 'user', text: input.text }, task_patch: { operation: 'place', post_action: 'focus_map' } } }] };
      return saved;
    });
    vi.mocked(api.prepareConversationTask).mockImplementation(async (_, __, input) => {
      saved = { ...saved, revision: 4, records: [...saved.records, { record_id: input.record_id, kind: 'tool_event', sequence: 4, created_at: '',
        payload: { content: { event: 'task_prepared', preparation: { task: saved.task, events: [{ state_version: 3 }], change_set: placementPreview } } } }] };
      return saved;
    });
    vi.mocked(api.confirmConversationProposal).mockImplementation(async () => {
      status = 'applied';
      return { plan_version: 3, state_version: 4, added_ids: ['new-tree'] } as Awaited<ReturnType<typeof api.confirmConversationProposal>>;
    });

    await act(async () => { await chat.send('Засей деревья и потом приблизь меня'); });
    expect(chat.focusRequest).toBeUndefined();
    await act(async () => { await chat.apply(); });
    expect(chat.focusRequest?.objectIds).toEqual(['new-tree']);
  });
  it('sends actual selection and preserves the legacy storage unchanged', async () => {
    await setup();
    act(() => chat.setSelectedIds(['tree']));
    await act(async () => { await chat.send('Эти деревья'); });
    expect(api.interpretConversationMessage).toHaveBeenCalledWith('test', 'conversation', expect.objectContaining({ map_context: { state_version: 3, object_ids: ['tree'] } }), expect.any(AbortSignal));
    expect(localStorage.getItem('green-project-chat-v1:test')).toBeNull();
  });
  it('does not prepare while the server requires clarification', async () => {
    await setup();
    vi.mocked(api.interpretConversationMessage).mockImplementation(async () => ({ ...saved, can_prepare: false }));
    await act(async () => { await chat.send('Посадите'); });
    expect(api.prepareConversationTask).not.toHaveBeenCalled();
  });
  it('checks current proposal status before applying', async () => {
    await setup();
    await act(async () => { await chat.send('Удалить'); });
    status = 'stale';
    await act(async () => { await chat.apply(); });
    expect(api.applyPlanChanges).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Пересчитать' })).toBeVisible();
  });
  it('ignores a late response after stopping', async () => {
    await setup();
    let resolve!: (value: Conversation) => void;
    vi.mocked(api.interpretConversationMessage).mockImplementation(() => new Promise(done => { resolve = done; }));
    let sending!: Promise<void>;
    act(() => { sending = chat.send('Удалить'); });
    await waitFor(() => expect(resolve).toBeDefined());
    act(() => chat.stop());
    await act(async () => { resolve(saved); await sending; });
    expect(api.prepareConversationTask).not.toHaveBeenCalled();
    expect(chat.pending).toBeUndefined();
  });
  it('retries interpretation with the same message ID after a lost response', async () => {
    await setup();
    vi.mocked(api.interpretConversationMessage).mockRejectedValueOnce(new Error('lost response'));
    await act(async () => { await chat.send('Удалить'); });
    const first = vi.mocked(api.interpretConversationMessage).mock.calls[0][2];
    await act(async () => { chat.retry(); });
    await waitFor(() => expect(api.interpretConversationMessage).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.interpretConversationMessage).mock.calls[1][2]).toEqual(first);
  });
  it('recovers a committed result after losing the apply response', async () => {
    await setup();
    await act(async () => { await chat.send('Удалить'); });
    vi.mocked(api.confirmConversationProposal).mockImplementation(async () => { status = 'applied'; throw new Error('lost response'); });
    await act(async () => { await chat.apply(); });
    expect(screen.getByText('Изменение применено.')).toBeVisible();
    expect(chat.proposal).toBeUndefined();
    await act(async () => { await chat.apply(); });
    expect(api.confirmConversationProposal).toHaveBeenCalledTimes(1);
  });
  it('persists decline without removing the original answer', async () => {
    await setup();
    await act(async () => { await chat.send('Удалить'); });
    await act(async () => { chat.discard(); });
    await waitFor(() => expect(chat.proposal).toBeUndefined());
    expect(api.declineConversationProposal).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Задание сохранено')).toBeVisible();
    expect(screen.getByText('Предложение отклонено.')).toBeVisible();
  });
  it('does not fall back to a raw mutation when confirmation detects a concurrent amendment', async () => {
    await setup();
    await act(async () => { await chat.send('Удалить'); });
    vi.mocked(api.confirmConversationProposal).mockImplementation(async () => {
      saved = { ...saved, revision: 5 };
      status = 'superseded';
      throw new Error('Задание изменилось');
    });
    await act(async () => { await chat.apply(); });
    expect(api.confirmConversationProposal).toHaveBeenCalledTimes(1);
    expect(api.applyPlanChanges).not.toHaveBeenCalled();
    expect(chat.proposal).toBeUndefined();
    await act(async () => { await chat.apply(); });
    expect(api.confirmConversationProposal).toHaveBeenCalledTimes(1);
  });
});
