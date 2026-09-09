import { describe, expect, it } from 'vitest';
import type { Conversation, Project } from '@green/api-client';
import { latestProposal } from './conversationProposal';

function proposal(requested: number, explanation?: string) {
  const conversation = { records: [{ record_id: 'preview', kind: 'tool_event', payload: { content: {
    event: 'task_prepared', preparation: {
      requested, shortfall_explanation: explanation, events: [{ state_version: 3 }],
      task: { values: { operation: 'place', scope: 'zones', zone_ids: ['six'] } },
      change_set: { id: 'p', digest: 'd', can_apply: true, additions: [{ id: 'one', kind: 'tree' }] },
    },
  } } }] } as unknown as Conversation;
  const project = { planting_zones: [{ id: 'six', label: 'Участок 6' }] } as Project;
  return latestProposal(conversation, project);
}

describe('proposal shortfall evidence', () => {
  it('keeps the requested count and supplied reason without inventing capacity', () => {
    const result = proposal(60, 'Часть позиций исключена из-за расстояний между посадками.');
    expect(result?.title).toBe('Найдено 1 из 60');
    expect(result?.shortfallExplanation).toBe('Часть позиций исключена из-за расстояний между посадками.');
  });
  it('does not manufacture a reason for older proposals', () => {
    expect(proposal(60)?.shortfallExplanation).toBeUndefined();
  });
  it('does not show an obsolete shortfall explanation when count is met', () => {
    expect(proposal(1, 'Недобор')?.shortfallExplanation).toBeUndefined();
  });
});
