import type { AgentMapControl } from '@/features/assistant/model/autonomous/autonomousControl';
import type { AutonomousPreview } from '@/features/assistant/model/autonomous/useAutonomousPreview';
import type {
  ChangeSetPreview,
  ConversationSummary,
  PlanChangeSetDraft,
  Project,
  ProjectChatReply,
} from '@green/api-client';
import { createContext, useContext } from 'react';

export interface ChatLine {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  result?: string;
  activity?: AgentTrace;
}
export interface AgentTraceEvent {
  ok?: boolean;
  tool?: string;
  effect?: string;
  summary?: {
    found?: number;
    requested?: number;
    can_apply?: boolean;
    species_revision_ids?: string[] | string;
    [key: string]: unknown;
  };
}
export interface AgentTrace {
  events?: AgentTraceEvent[];
  preview_attempts?: number;
  tool_calls?: number;
}
export interface Proposal {
  messageId: string;
  recordId?: string;
  scopeLabel?: string;
  verificationNotice?: string;
  shortfallExplanation?: string;
  postAction?: 'focus_map';
  agentTrace?: AgentTrace;
  preview: ChangeSetPreview;
  draft?: PlanChangeSetDraft;
  intent: ProjectChatReply;
  stateVersion: number;
  title: string;
  stale?: boolean;
}
export interface AssistantFocusRequest {
  token: string;
  objectIds: string[];
}
export interface PendingIntent {
  messageId: string;
  intent: ProjectChatReply;
}
export interface AssistantContextValue {
  projectId: string;
  project?: Project;
  open: boolean;
  setOpen: (open: boolean) => void;
  messages: ChatLine[];
  draft: string;
  setDraft: (value: string) => void;
  conversations: ConversationSummary[];
  activeConversationId?: string;
  conversationTitle: string;
  listOpen: boolean;
  showConversations: () => Promise<void>;
  closeConversations: () => void;
  selectConversation: (id: string) => Promise<void>;
  newConversation: () => Promise<void>;
  pending?: 'loading' | 'thinking' | 'preparing' | 'applying' | 'undoing';
  error?: string;
  proposal?: Proposal;
  pendingIntent?: PendingIntent;
  undoReceipt?: { messageId: string; version: number; stateVersion: number };
  focusRequest?: AssistantFocusRequest;
  autonomousPreview?: AutonomousPreview;
  setAutonomousPreview?: (value: AutonomousPreview | undefined) => void;
  executeMapControl?: AgentMapControl;
  registerMapControl?: (adapter: AgentMapControl) => () => void;
  assistantMode?: 'legacy' | 'autonomous';
  setAssistantMode?: (value: 'legacy' | 'autonomous') => void;
  send: (text?: string) => Promise<void>;
  stop: () => void;
  retry: () => void;
  chooseZone: (id: string) => void;
  apply: () => Promise<void>;
  discard: () => void;
  recalculate: () => void;
  undo: () => Promise<void>;
  selectedIds: string[];
  setSelectedIds: (ids: string[]) => void;
  selectedZoneIds: string[];
  setSelectedZoneIds: (ids: string[]) => void;
  horizon?: number;
  setHorizon: (horizon: number) => void;
  clearHistory: () => void;
  width: number;
  minWidth: number;
  maxWidth: number;
  setWidth: (value: number) => void;
  resetWidth: () => void;
}
export const AssistantContext = createContext<AssistantContextValue | null>(
  null,
);
export function useProjectAssistant() {
  const value = useContext(AssistantContext);
  if (!value) throw new Error('ProjectAssistantProvider is required');
  return value;
}

export function assistantWidth(viewport: number, fraction: number) {
  const min = Math.min(320, Math.max(240, viewport - 48));
  const max = Math.max(min, Math.min(528, viewport * 0.46));
  return {
    min,
    max,
    value: Math.round(
      Math.max(
        min,
        Math.min(
          max,
          viewport *
            (Number.isFinite(fraction) && fraction > 0 && fraction < 1
              ? fraction
              : 0.31),
        ),
      ),
    ),
  };
}
