import { describe, expect, it } from 'vitest';
import type { PlanHistoryState, Project } from '@green/api-client';
import { captureHistoryCommand, historyGoalMatches } from './historyProof';

const project = (state = 7, version = 4) =>
  ({
    id: 'project',
    state_version: state,
    plan: { id: 'plan', version },
  }) as Project;
const history = (
  cursor = 23,
  undo = 'change-23',
  redo: string | null = 'change-24',
) =>
  ({
    can_undo: true,
    can_redo: Boolean(redo),
    entries: [],
    cursor_ordinal: cursor,
    undo_target_id: undo,
    redo_target_id: redo,
  }) as PlanHistoryState & {
    cursor_ordinal: number;
    undo_target_id: string;
    redo_target_id: string | null;
  };

describe('history cursor state proof', () => {
  it('captures a target outside the displayed 20 entries and rejects legacy unknown targets', () => {
    expect(
      captureHistoryCommand('project', project(), history(), 'undo'),
    ).toMatchObject({ cursor: 23, targetId: 'change-23' });
    expect(() =>
      captureHistoryCommand(
        'project',
        project(),
        { can_undo: true, can_redo: false, entries: [] } as PlanHistoryState,
        'undo',
      ),
    ).toThrow(/точное/);
  });
  it('requires both revisions and the captured target at the exact next cursor', () => {
    const context = captureHistoryCommand(
      'project',
      project(),
      history(),
      'undo',
    );
    const applied = history(22, 'change-22', 'change-23');
    expect(historyGoalMatches(context, project(8, 5), applied)).toBe(true);
    expect(historyGoalMatches(context, project(7, 5), applied)).toBe(false);
    expect(historyGoalMatches(context, project(8, 4), applied)).toBe(false);
    expect(historyGoalMatches(context, project(9, 6), history())).toBe(false);
    expect(
      historyGoalMatches(
        context,
        project(8, 5),
        history(22, 'change-22', 'another-change'),
      ),
    ).toBe(false);
    expect(
      historyGoalMatches(
        context,
        project(9, 6),
        history(21, 'change-21', 'change-23'),
      ),
    ).toBe(false);
    expect(
      historyGoalMatches(
        context,
        {
          ...project(8, 5),
          plan: { ...project(8, 5).plan!, id: 'replacement-plan' },
        },
        applied,
      ),
    ).toBe(false);
  });
  it('proves redo with the same complete-journal target while the display rows are empty', () => {
    const context = captureHistoryCommand(
      'project',
      project(),
      history(),
      'redo',
    );
    expect(
      historyGoalMatches(
        context,
        project(8, 5),
        history(24, 'change-24', 'change-25'),
      ),
    ).toBe(true);
    expect(
      historyGoalMatches(
        context,
        project(8, 5),
        history(24, 'unrelated', 'change-25'),
      ),
    ).toBe(false);
  });
});
