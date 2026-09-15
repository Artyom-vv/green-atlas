import {
  normalizeAgentRun,
  normalizeAgentRuns,
  normalizeControlProof,
} from '../adapters/agent';
import {
  normalizeChangeSetPreview,
  normalizeZoneChangePreview,
} from '../adapters/previews';
import type {
  AgentControlResult,
  AgentSelectionContext,
  Conversation,
  ConversationMessageInput,
  ConversationPrepareInput,
  ConversationProposalStatus,
  ConversationSummary,
  LegacyChatImport,
  PlanMutationResult,
  PlanningBrief,
  ProjectChatInput,
  ProjectChatReply,
} from '../contracts';
import type { WireSchema } from '../contracts/wire';
import type { components } from '../schema';
import { json, request } from '../transport/request';

export const assistantApi = {
  planningAssistantStatus: () =>
    request<components['schemas']['AssistantStatus']>(
      '/api/planning-assistant/status',
    ),
  interpretPlanningTask: (task: string, signal?: AbortSignal) =>
    request<PlanningBrief>('/api/planning-assistant/interpret', {
      ...json({ task }),
      signal,
    }),
  projectChat: (input: ProjectChatInput, signal?: AbortSignal) =>
    request<ProjectChatReply>('/api/project-assistant/chat', {
      ...json(input),
      signal,
    }),
  createAgentRun: (
    projectId: string,
    text: string,
    conversationId?: string,
    signal?: AbortSignal,
    selectionContext?: AgentSelectionContext,
  ) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' + encodeURIComponent(projectId) + '/agent-runs',
      {
        ...json({
          text,
          conversation_id: conversationId,
          selection_context: selectionContext,
        }),
        signal,
      },
    ).then(normalizeAgentRun),
  listAgentRuns: (projectId: string, limit = 20) =>
    request<WireSchema<'AgentRunRecord'>[]>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs?limit=' +
        limit,
    ).then(normalizeAgentRuns),
  getAgentRun: (projectId: string, runId: string) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId),
    ).then(normalizeAgentRun),
  getAgentRunPreview: (projectId: string, runId: string, previewRef: string) =>
    request<WireSchema<'ChangeSetPreview'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/preview?preview_ref=' +
        encodeURIComponent(previewRef),
    ).then(normalizeChangeSetPreview),
  getAgentRunZonePreview: (
    projectId: string,
    runId: string,
    previewRef: string,
  ) =>
    request<WireSchema<'ZoneChangePreview'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/zone-preview?preview_ref=' +
        encodeURIComponent(previewRef),
    ).then(normalizeZoneChangePreview),
  runAgentRun: (projectId: string, runId: string, signal?: AbortSignal) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/run',
      { ...json(), signal },
    ).then(normalizeAgentRun),
  getAgentControlCommand: (
    projectId: string,
    runId: string,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'ControlCommandGeometry'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/control-command',
      { signal },
    ).then(normalizeControlProof),
  reportAgentControlResult: (
    projectId: string,
    runId: string,
    result: AgentControlResult,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/control-result',
      { ...json(result), signal },
    ).then(normalizeAgentRun),
  cancelAgentRun: (projectId: string, runId: string) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/cancel',
      json(),
    ).then(normalizeAgentRun),
  resumeAgentRun: (projectId: string, runId: string) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/resume',
      json(),
    ).then(normalizeAgentRun),
  answerAgentRun: (
    projectId: string,
    runId: string,
    text: string,
    selectionContext?: AgentSelectionContext,
  ) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/answer',
      json({ text, selection_context: selectionContext }),
    ).then(normalizeAgentRun),
  approveAgentRun: (projectId: string, runId: string, previewRef?: string) =>
    request<WireSchema<'AgentRunRecord'>>(
      '/api/projects/' +
        encodeURIComponent(projectId) +
        '/agent-runs/' +
        encodeURIComponent(runId) +
        '/approve',
      json(previewRef ? { preview_ref: previewRef } : {}),
    ).then(normalizeAgentRun),
  listConversations: (projectId: string) =>
    request<ConversationSummary[]>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations`,
    ),
  createConversation: (
    projectId: string,
    title = 'Новый диалог',
    requestId?: string,
  ) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations`,
      json({ title, request_id: requestId }),
    ),
  getConversation: (projectId: string, conversationId: string) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}`,
    ),
  importConversation: (projectId: string, input: LegacyChatImport) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/import`,
      json(input),
    ),
  appendConversationMessage: (
    projectId: string,
    conversationId: string,
    input: ConversationMessageInput,
  ) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/messages`,
      json(input),
    ),
  interpretConversationMessage: (
    projectId: string,
    conversationId: string,
    input: ConversationMessageInput,
    signal?: AbortSignal,
  ) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/interpret`,
      { ...json(input), signal },
    ),
  prepareConversationTask: (
    projectId: string,
    conversationId: string,
    input: ConversationPrepareInput,
    signal?: AbortSignal,
  ) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/prepare`,
      { ...json(input), signal },
    ),
  getConversationProposalStatus: (
    projectId: string,
    conversationId: string,
    recordId: string,
  ) =>
    request<ConversationProposalStatus>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/proposals/${encodeURIComponent(recordId)}/status`,
    ),
  declineConversationProposal: (
    projectId: string,
    conversationId: string,
    recordId: string,
    input: ConversationPrepareInput,
  ) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/proposals/${encodeURIComponent(recordId)}/decline`,
      json(input),
    ),
  confirmConversationProposal: (
    projectId: string,
    conversationId: string,
    recordId: string,
    input: components['schemas']['ConfirmProposal'],
  ) =>
    request<PlanMutationResult>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/proposals/${encodeURIComponent(recordId)}/confirm`,
      json(input),
    ),
};
