import type { Schemas } from './wire';

export type PlanningBrief = Schemas['PlanningBrief'];

export type ProjectChatInput = Schemas['ProjectChatInput'];

export type ProjectChatReply = Schemas['ProjectChatReply'];

export type Conversation = Schemas['Conversation'];

export type ConversationSummary = Schemas['ConversationSummary'];

export type LegacyChatImport = Schemas['LegacyImport'];

export type ConversationMessageInput = Schemas['UserMessage'];

export type ConversationPrepareInput = Schemas['PrepareTask'];

export type ConversationProposalStatus = Schemas['ProposalStatus'];

/** Client-owned selection always carries explicit collections. */
export type AgentSelectionContext = Omit<
  Schemas['SelectionContext'],
  'zone_ids' | 'object_ids'
> &
  Required<Pick<Schemas['SelectionContext'], 'zone_ids' | 'object_ids'>>;
export type AgentRunEvent = Omit<Schemas['AgentRunEvent'], 'payload'> &
  Required<Pick<Schemas['AgentRunEvent'], 'payload'>>;
export type AgentControlCommand = Omit<Schemas['ControlCommand'], 'bounds'> & {
  bounds: [number, number, number, number];
};
export type AgentControlResult = Schemas['ControlResult'];
export type AgentControlProof = Omit<
  Schemas['ControlCommandGeometry'],
  'command'
> & { command: AgentControlCommand };

type AgentCollectionDefaults =
  'candidate_zone_ids' | 'tool_calls' | 'tool_fingerprints' | 'evidence_refs';
export type AgentRunState = Omit<
  Schemas['AgentRunState'],
  AgentCollectionDefaults | 'control_command'
> &
  Required<Pick<Schemas['AgentRunState'], AgentCollectionDefaults>> & {
    control_command?: AgentControlCommand | null;
  };
export type AgentRun = Omit<Schemas['AgentRunRecord'], 'state' | 'events'> & {
  state: AgentRunState;
  events: AgentRunEvent[];
};
