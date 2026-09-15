import { assistantProjectQuery } from '@/features/assistant/api/queries';
import { openProjectConversation } from '@/features/assistant/api/serverConversations';
import {
  type AssistantContextValue,
  type ChatLine,
  type Proposal,
} from '@/features/assistant/model/assistantContext';
import type { AgentMapControl } from '@/features/assistant/model/autonomous/autonomousControl';
import type { AutonomousPreview } from '@/features/assistant/model/autonomous/useAutonomousPreview';
import { latestProposal } from '@/features/assistant/model/conversation/conversationProposal';
import { conversationMessages } from '@/features/assistant/model/conversation/messages';
import { useAssistantDraft } from '@/features/assistant/model/useAssistantDraft';
import { useAssistantLayout } from '@/features/assistant/model/useAssistantLayout';
import {
  api,
  type Conversation,
  type ConversationMessageInput,
  type ConversationSummary,
} from '@green/api-client';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
const uid = () => crypto.randomUUID();
const errorText = (e: unknown) =>
  e instanceof Error ? e.message : 'Не удалось завершить запрос.';
type Turn = {
  text: string;
  id: string;
  prepareId: string;
  input?: ConversationMessageInput;
};
export function useConversationSession(projectId: string) {
  const queryClient = useQueryClient();
  const { data: project } = useQuery(assistantProjectQuery(projectId));
  const latestProject = useRef(project);
  latestProject.current = project;
  const conversation = useRef<Conversation | undefined>(undefined);
  const opening = useRef<Promise<Conversation> | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatLine[]>([]);
  const { form: draftForm, draft, setDraft } = useAssistantDraft();
  const drafts = useRef(new Map<string, string>());
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string>();
  const [conversationTitle, setConversationTitle] = useState('Чат проекта');
  const [listOpen, setListOpen] = useState(false);
  const creationId = useRef<string | undefined>(undefined);
  const [pending, setPending] = useState<AssistantContextValue['pending']>();
  const busy = useRef(false);
  const [error, setError] = useState<string>();
  const [proposal, setProposal] = useState<Proposal>();
  const [autonomousPreview, setAutonomousPreview] =
    useState<AutonomousPreview>();
  const mapControl = useRef<AgentMapControl | undefined>(undefined);
  const registerMapControl = useCallback((adapter: AgentMapControl) => {
    mapControl.current = adapter;
    return () => {
      if (mapControl.current === adapter) mapControl.current = undefined;
    };
  }, []);
  const executeMapControl = useCallback<AgentMapControl>(
    (command, geometry, signal) =>
      mapControl.current
        ? mapControl.current(command, geometry, signal)
        : Promise.resolve({ status: 'failed', error_code: 'MAP_UNAVAILABLE' }),
    [],
  );
  const [assistantMode, setAssistantMode] = useState<'legacy' | 'autonomous'>(
    'legacy',
  );
  const [focusRequest, setFocusRequest] =
    useState<AssistantContextValue['focusRequest']>();
  const [undoReceipt, setUndoReceipt] =
    useState<AssistantContextValue['undoReceipt']>();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedZoneIds, setSelectedZoneIds] = useState<string[]>([]);
  const [horizon, setHorizon] = useState<number>();
  const controller = useRef<AbortController | undefined>(undefined);
  const generation = useRef(0);
  const lastTurn = useRef<Turn | undefined>(undefined);
  const { width, setWidth, resetWidth } = useAssistantLayout();
  useEffect(
    () => () => {
      generation.current++;
      controller.current?.abort();
    },
    [],
  );
  useEffect(() => {
    if (!autonomousPreview) return;
    const preview = autonomousPreview.preview;
    if (
      project &&
      (project.state_version !== autonomousPreview.stateVersion ||
        (project.plan?.version ?? null) !==
          (preview.base_plan_version ?? null) ||
        (autonomousPreview.kind === 'planting_zones' &&
          project.geometry_version !==
            autonomousPreview.preview.base_geometry_version))
    ) {
      setAutonomousPreview(undefined);
      return;
    }
    const remaining = Date.parse(preview.expires_at) - Date.now();
    const timer = window.setTimeout(
      () => setAutonomousPreview(undefined),
      Math.max(0, Math.min(2147483647, remaining)),
    );
    return () => window.clearTimeout(timer);
  }, [project, autonomousPreview]);
  useEffect(() => {
    if (!proposal || proposal.stale) return;
    if (
      project &&
      (project.state_version !== proposal.stateVersion ||
        project.plan?.version !== proposal.preview.base_plan_version)
    ) {
      setProposal((current) =>
        current ? { ...current, stale: true } : undefined,
      );
      return;
    }
    const timer = window.setTimeout(
      () =>
        setProposal((current) =>
          current ? { ...current, stale: true } : undefined,
        ),
      Math.max(
        0,
        Math.min(
          2147483647,
          Date.parse(proposal.preview.expires_at) - Date.now(),
        ),
      ),
    );
    return () => window.clearTimeout(timer);
  }, [project, proposal]);
  const refresh = useCallback(async () => {
    await Promise.all(
      [
        'workspace-project',
        'setup-project',
        'plan-history',
        'data-passport',
      ].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key, projectId] }),
      ),
    );
  }, [projectId, queryClient]);
  const ensure = async () => {
    if (conversation.current) return conversation.current;
    opening.current ??= openProjectConversation(projectId).finally(() => {
      opening.current = undefined;
    });
    return opening.current;
  };
  const show = async (chat: Conversation, token: number) => {
    if (token !== generation.current) return;
    conversation.current = chat;
    setActiveConversationId(chat.id);
    setConversationTitle(chat.title);
    const lines: ChatLine[] = conversationMessages(chat);
    const proposed = latestProject.current
      ? latestProposal(chat, latestProject.current)
      : undefined;
    let visible: Proposal | undefined;
    if (proposed?.recordId) {
      const sequence =
        chat.records.find((record) => record.record_id === proposed.recordId)
          ?.sequence ?? Infinity;
      const nextMessage = chat.records.find(
        (record) => record.kind === 'message' && record.sequence > sequence,
      );
      const position = nextMessage
        ? lines.findIndex((line) => line.id === nextMessage.record_id)
        : -1;
      const insertProposal = (line: ChatLine) =>
        lines.splice(position < 0 ? lines.length : position, 0, line);
      const state = await api.getConversationProposalStatus(
        projectId,
        chat.id,
        proposed.recordId,
      );
      if (token !== generation.current) return;
      if (
        ['ready', 'blocked', 'expired', 'stale', 'unavailable'].includes(
          state.status,
        )
      ) {
        visible = {
          ...proposed,
          stale: !['ready', 'blocked'].includes(state.status),
        };
        insertProposal({ id: proposed.messageId, role: 'assistant', text: '' });
      } else {
        if (state.status === 'applied' || state.status === 'undone')
          setError(undefined);
        const text =
          state.status === 'applied'
            ? 'Изменение применено.'
            : state.status === 'undone'
              ? 'Изменение отменено.'
              : state.status === 'declined'
                ? 'Предложение отклонено.'
                : 'Предложение заменено.';
        insertProposal({ id: proposed.messageId, role: 'assistant', text });
      }
    }
    setMessages(lines);
    setProposal(visible);
  };
  const showConversations = async () => {
    if (busy.current) return;
    busy.current = true;
    setPending('loading');
    setError(undefined);
    setListOpen(true);
    const token = ++generation.current;
    try {
      const items = await api.listConversations(projectId);
      if (token === generation.current) setConversations(items);
    } catch (e) {
      if (token === generation.current) setError(errorText(e));
    } finally {
      if (token === generation.current) {
        busy.current = false;
        setPending(undefined);
      }
    }
  };
  const switchConversation = async (id?: string) => {
    if (busy.current) return;
    busy.current = true;
    setPending('loading');
    setError(undefined);
    const token = ++generation.current;
    const previous = conversation.current;
    try {
      // Reuse the creation key after an uncertain response: do not duplicate a chat.
      if (!id) creationId.current ??= uid();
      const next = id
        ? await api.getConversation(projectId, id)
        : await api.createConversation(
            projectId,
            'Новый диалог',
            creationId.current,
          );
      if (token !== generation.current) return;
      if (previous) drafts.current.set(previous.id, draft);
      await show(next, token);
      if (token !== generation.current) return;
      setUndoReceipt(undefined);
      lastTurn.current = undefined;
      setDraft(drafts.current.get(next.id) ?? '');
      setListOpen(false);
      try {
        localStorage.setItem(`green-active-conversation:${projectId}`, next.id);
      } catch {
        /* The conversation remains saved on the server. */
      }
      if (!id) creationId.current = undefined;
    } catch (e) {
      if (token === generation.current) {
        conversation.current = previous;
        setActiveConversationId(previous?.id);
        setConversationTitle(previous?.title ?? 'Чат проекта');
        setError(errorText(e));
      }
    } finally {
      if (token === generation.current) {
        busy.current = false;
        setPending(undefined);
      }
    }
  };
  const load = async () => {
    if (!projectId || busy.current) return;
    busy.current = true;
    setPending('loading');
    setError(undefined);
    const token = ++generation.current;
    try {
      await show(await ensure(), token);
    } catch (e) {
      if (token === generation.current) setError(errorText(e));
    } finally {
      if (token === generation.current) {
        busy.current = false;
        setPending(undefined);
      }
    }
  };
  const run = async (turn: Turn) => {
    if (busy.current || !latestProject.current) return;
    busy.current = true;
    setPending('thinking');
    setError(undefined);
    setProposal(undefined);
    const token = ++generation.current;
    const abort = new AbortController();
    controller.current = abort;
    try {
      const initial = await ensure();
      if (token !== generation.current) return;
      const p = latestProject.current;
      turn.input ??= {
        record_id: turn.id,
        expected_revision: initial.revision,
        text: turn.text,
        map_context:
          selectedIds.length || selectedZoneIds.length
            ? {
                state_version: p.state_version,
                object_ids: selectedIds,
                ...(selectedZoneIds.length
                  ? { zone_ids: selectedZoneIds }
                  : {}),
              }
            : undefined,
      };
      setMessages((current) =>
        current.some((line) => line.id === turn.id)
          ? current
          : [...current, { id: turn.id, role: 'user', text: turn.text }],
      );
      const interpreted = await api.interpretConversationMessage(
        projectId,
        initial.id,
        turn.input,
        abort.signal,
      );
      if (token !== generation.current) return;
      conversation.current = interpreted;
      setMessages(conversationMessages(interpreted));
      const source = interpreted.records.find(
        (record) => record.record_id === turn.id,
      );
      if (
        interpreted.can_prepare &&
        Object.keys(source?.payload.task_patch ?? {}).length
      ) {
        setPending('preparing');
        const prepared = await api.prepareConversationTask(
          projectId,
          interpreted.id,
          {
            record_id: turn.prepareId,
            expected_revision: interpreted.revision,
          },
          abort.signal,
        );
        await show(prepared, token);
      } else await show(interpreted, token);
    } catch (e) {
      if (!abort.signal.aborted && token === generation.current)
        setError(errorText(e));
    } finally {
      if (token === generation.current) {
        busy.current = false;
        setPending(undefined);
      }
    }
  };
  const send = async (text = draft) => {
    if (!text.trim() || text.length > 2000 || busy.current) return;
    const turn = { text: text.trim(), id: uid(), prepareId: uid() };
    lastTurn.current = turn;
    setDraft('');
    await run(turn);
  };
  const stop = () => {
    if (pending !== 'thinking' && pending !== 'preparing') return;
    generation.current++;
    controller.current?.abort();
    busy.current = false;
    setPending(undefined);
    setError('Запрос остановлен. Изменения плана не применялись.');
  };
  const recalculate = async () => {
    if (busy.current || !conversation.current) return;
    busy.current = true;
    setPending('preparing');
    setError(undefined);
    const token = ++generation.current;
    const abort = new AbortController();
    controller.current = abort;
    try {
      const chat = await api.getConversation(
        projectId,
        conversation.current.id,
      );
      const prepared = await api.prepareConversationTask(
        projectId,
        chat.id,
        { record_id: uid(), expected_revision: chat.revision },
        abort.signal,
      );
      await show(prepared, token);
    } catch (e) {
      if (token === generation.current && !abort.signal.aborted)
        setError(errorText(e));
    } finally {
      if (token === generation.current) {
        busy.current = false;
        setPending(undefined);
      }
    }
  };
  const discard = async () => {
    if (busy.current || !proposal?.recordId || !conversation.current) return;
    busy.current = true;
    setPending('preparing');
    setError(undefined);
    const token = ++generation.current;
    try {
      const chat = await api.declineConversationProposal(
        projectId,
        conversation.current.id,
        proposal.recordId,
        { record_id: uid(), expected_revision: conversation.current.revision },
      );
      await show(chat, token);
    } catch (e) {
      setError(errorText(e));
    } finally {
      busy.current = false;
      setPending(undefined);
    }
  };
  const apply = async () => {
    const current = proposal,
      chat = conversation.current;
    if (
      busy.current ||
      !current?.recordId ||
      current.stale ||
      !current.preview.can_apply ||
      !chat
    )
      return;
    busy.current = true;
    setPending('applying');
    setError(undefined);
    const token = ++generation.current;
    try {
      const status = await api.getConversationProposalStatus(
        projectId,
        chat.id,
        current.recordId,
      );
      if (!status.can_apply) {
        await show(chat, token);
        return;
      }
      const changed = await api.confirmConversationProposal(
        projectId,
        chat.id,
        current.recordId,
        { expected_revision: chat.revision },
      );
      setUndoReceipt({
        messageId: current.messageId,
        version: changed.plan_version,
        stateVersion: changed.state_version,
      });
      const addedIds = changed.added_ids ?? [];
      if (current.postAction === 'focus_map' && addedIds.length) {
        setFocusRequest({ token: uid(), objectIds: addedIds });
      }
      await refresh();
      await show(await api.getConversation(projectId, chat.id), token);
    } catch (e) {
      setError(errorText(e));
      setProposal(undefined);
      await refresh();
      try {
        await show(await api.getConversation(projectId, chat.id), token);
      } catch {
        /* Unknown result stays non-executable. */
      }
    } finally {
      busy.current = false;
      setPending(undefined);
    }
  };
  const undo = async () => {
    if (busy.current || !undoReceipt) return;
    busy.current = true;
    setPending('undoing');
    setError(undefined);
    try {
      const fresh = await api.getProject(projectId, false);
      if (
        fresh.state_version !== undoReceipt.stateVersion ||
        fresh.plan?.version !== undoReceipt.version
      )
        throw new Error('План уже изменён. Используйте историю изменений.');
      await api.undoPlanChange(projectId);
      await refresh();
      if (conversation.current)
        await show(
          await api.getConversation(projectId, conversation.current.id),
          generation.current,
        );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setUndoReceipt(undefined);
      busy.current = false;
      setPending(undefined);
    }
  };
  const value: AssistantContextValue = {
    projectId,
    project,
    open,
    setOpen: (value) => {
      setOpen(value);
      if (value && !conversation.current) void load();
    },
    messages,
    draft,
    setDraft,
    pending,
    error,
    proposal,
    undoReceipt,
    focusRequest,
    autonomousPreview,
    setAutonomousPreview,
    executeMapControl,
    registerMapControl,
    assistantMode,
    setAssistantMode,
    conversations,
    activeConversationId,
    conversationTitle,
    listOpen,
    showConversations,
    closeConversations: () => {
      if (!busy.current) {
        setListOpen(false);
        setError(undefined);
      }
    },
    selectConversation: (id) => switchConversation(id),
    newConversation: () => switchConversation(),
    send,
    stop,
    retry: () => {
      if (lastTurn.current) void run(lastTurn.current);
      else void load();
    },
    apply,
    undo,
    discard: () => void discard(),
    recalculate: () => void recalculate(),
    chooseZone: () => {},
    selectedIds,
    setSelectedIds,
    selectedZoneIds,
    setSelectedZoneIds,
    horizon,
    setHorizon,
    clearHistory: () => {},
    width: width.value,
    minWidth: width.min,
    maxWidth: width.max,
    setWidth,
    resetWidth,
  };
  return { value, draftForm };
}
