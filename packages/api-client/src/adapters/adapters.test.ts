import { describe, expect, it } from 'vitest';
import type { WireSchema } from '../contracts/wire';
import { ApiClientError } from '../transport/errors';
import { normalizeAgentRun, normalizeControlCommand } from './agent';
import {
  normalizeBrushPreview,
  normalizeChangeSetPreview,
  normalizePatternPreview,
} from './previews';
import { normalizeScene } from './scene';

const changeSet: WireSchema<'ChangeSetPreview'> = {
  id: 'preview-1',
  digest: 'digest',
  base_plan_version: 3,
  source: 'pattern',
  label: 'Ряд',
  can_apply: true,
  expires_at: '2026-09-15T00:00:00Z',
};

const command: WireSchema<'ControlCommand'> = {
  id: 'command-1',
  action: 'focus_zone',
  project_id: 'project-1',
  run_id: 'run-1',
  execution_attempt_id: 'attempt-1',
  zone_id: 'zone-1',
  zone_label: 'Сквер',
  state_version: 3,
  geometry_version: 1,
  geometry_digest: 'geometry-digest',
  bounds: [0, 10, 30, 40],
  issued_at: '2026-09-14T10:00:00Z',
};

const agentRun: WireSchema<'AgentRunRecord'> = {
  state: {
    run_id: 'run-1',
    project_id: 'project-1',
    status: 'waiting_ui',
    intent: {
      raw_text: 'Покажи сквер',
      goal: { operation: 'inspect' },
      scope_mode: 'explicit',
    },
    snapshot_version: 3,
    step: 1,
    max_steps: 64,
    control_command: command,
  },
  revision: 1,
  created_at: '2026-09-14T10:00:00Z',
  updated_at: '2026-09-14T10:00:00Z',
};

describe('wire response adapters', () => {
  it('retains the server preview identity and rejects an absent identity', () => {
    const source = Object.freeze({ ...changeSet });
    expect(normalizeChangeSetPreview(source)).toEqual(source);
    const { id: _id, ...withoutId } = source;
    expect(() => normalizeChangeSetPreview(withoutId)).toThrow(ApiClientError);
  });

  it('normalizes the optional preview collections without mutating source', () => {
    const pattern: WireSchema<'PatternPreview'> = {
      pattern_id: 'row-1',
      type: 'row',
      requested_count: 10,
      generated_count: 10,
      accepted_count: 10,
      rejected_count: 0,
      capacity_shortfall: 0,
      data_confidence: 'verified',
      change_set: changeSet,
    };
    const before = structuredClone(pattern);
    Object.freeze(pattern);
    Object.freeze(pattern.change_set);
    const normalized = normalizePatternPreview(pattern);
    expect(normalized.skipped).toEqual([]);
    expect(normalized.change_set?.id).toBe('preview-1');
    expect(normalized.change_set).not.toBe(pattern.change_set);
    expect(pattern).toEqual(before);

    const brush: WireSchema<'BrushPreview'> = {
      brush_id: 'brush-1',
      requested_count: 2,
      accepted_count: 2,
      added_count: 2,
      removed_count: 0,
      change_set: null,
    };
    expect(normalizeBrushPreview(Object.freeze(brush))).toEqual({
      ...brush,
      skipped: [],
    });
    expect(brush).not.toHaveProperty('skipped');
  });

  it('copies normalized agent collections while preserving missing facts', () => {
    const source = structuredClone(agentRun);
    source.events = [
      { sequence: 1, kind: 'created', created_at: source.created_at },
    ];
    const before = structuredClone(source);
    Object.freeze(source.state);
    Object.freeze(source.events[0]);
    const result = normalizeAgentRun(Object.freeze(source));
    expect(result.state.candidate_zone_ids).toEqual([]);
    expect(result.state.tool_calls).toEqual([]);
    expect(result.events[0].payload).toEqual({});
    expect(result.state.resolved_scope).toBeUndefined();
    expect(result.state.control_command?.bounds).toEqual([0, 10, 30, 40]);
    expect(result.state.control_command?.bounds).not.toBe(command.bounds);
    expect(source).toEqual(before);
  });

  it.each([
    [0, 1, 2],
    [0, 1, 2, 3, 4],
    [0, 1, Infinity, 3],
  ])('rejects unusable control bounds %s', (...bounds) => {
    expect(() => normalizeControlCommand({ ...command, bounds })).toThrow(
      'Сервер вернул некорректные границы области карты.',
    );
  });

  it('normalizes scene collections without inventing height evidence', () => {
    const source: WireSchema<'SceneSnapshot'> = {
      plan_version: 3,
      horizon_year: 0,
      coordinate_origin: [0, 0],
      georeference_status: 'missing',
      geometry_source: 'missing',
      completeness: 'partial',
      terrain_status: 'missing',
      building_heights_status: 'missing',
      building_feature_count: 0,
      building_height_confirmed_count: 0,
      note: 'Нет данных высот',
    };
    const before = structuredClone(source);
    const result = normalizeScene(Object.freeze(source));
    expect(result.objects).toEqual([]);
    expect(result.data_gaps).toEqual([]);
    expect(result.terrain_elevation_m).toBeUndefined();
    expect(source).toEqual(before);
  });
});
