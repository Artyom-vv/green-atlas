import type {
  AgentControlCommand,
  AgentControlProof,
  AgentRun,
  AgentRunEvent,
} from '../contracts';
import type { WireSchema } from '../contracts/wire';
import { ApiClientError } from '../transport/errors';

export function normalizeControlCommand(
  source: WireSchema<'ControlCommand'>,
): AgentControlCommand {
  const bounds = source.bounds;
  if (
    !Array.isArray(bounds) ||
    bounds.length !== 4 ||
    !bounds.every(Number.isFinite)
  )
    throw new ApiClientError(
      'INVALID_API_RESPONSE',
      'Сервер вернул некорректные границы области карты.',
    );
  return { ...source, bounds: [bounds[0], bounds[1], bounds[2], bounds[3]] };
}
export function normalizeControlProof(
  source: WireSchema<'ControlCommandGeometry'>,
): AgentControlProof {
  return { ...source, command: normalizeControlCommand(source.command) };
}
function normalizeEvent(source: WireSchema<'AgentRunEvent'>): AgentRunEvent {
  return { ...source, payload: { ...(source.payload ?? {}) } };
}
export function normalizeAgentRun(
  source: WireSchema<'AgentRunRecord'>,
): AgentRun {
  const state = source.state;
  return {
    ...source,
    events: (source.events ?? []).map(normalizeEvent),
    state: {
      ...state,
      candidate_zone_ids: [...(state.candidate_zone_ids ?? [])],
      tool_calls: [...(state.tool_calls ?? [])],
      tool_fingerprints: [...(state.tool_fingerprints ?? [])],
      evidence_refs: [...(state.evidence_refs ?? [])],
      control_command: state.control_command
        ? normalizeControlCommand(state.control_command)
        : state.control_command,
    },
  };
}
export function normalizeAgentRuns(
  source: WireSchema<'AgentRunRecord'>[],
): AgentRun[] {
  return source.map(normalizeAgentRun);
}
