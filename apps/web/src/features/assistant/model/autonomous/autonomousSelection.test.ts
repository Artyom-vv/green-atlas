import { snapshotSelection } from '@/features/assistant/model/autonomous/autonomousSelection';
import {
  api,
  type AgentRun,
  type AgentSelectionContext,
} from '@green/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';

const selection = (): AgentSelectionContext => ({
  project_id: 'selection-project',
  state_version: 7,
  plan_version: 3,
  zone_ids: ['zone'],
  object_ids: ['tree'],
});
afterEach(() => vi.unstubAllGlobals());

function response() {
  const result: AgentRun = {
    revision: 1,
    created_at: '',
    updated_at: '',
    events: [],
    state: {
      run_id: 'run',
      project_id: 'selection-project',
      status: 'queued',
      intent: {
        raw_text: 'Проверь участок',
        goal: { operation: 'inspect' },
        scope_mode: 'explicit',
      },
      candidate_zone_ids: [],
      snapshot_version: 7,
      step: 0,
      max_steps: 64,
      tool_calls: [],
      tool_fingerprints: [],
      evidence_refs: [],
    },
  };
  return new Response(JSON.stringify(result));
}

describe('autonomous selection transport', () => {
  it('sends the immutable map snapshot separately from source text and keeps create conversation/signal compatibility', async () => {
    const fetch = vi.fn().mockResolvedValue(response());
    vi.stubGlobal('fetch', fetch);
    const current = selection();
    const snapshot = snapshotSelection('selection-project', current)!;
    current.object_ids.push('later-tree');
    current.zone_ids.splice(0);
    current.state_version = 8;
    const signal = new AbortController().signal;
    await api.createAgentRun(
      'selection-project',
      'Проверь участок 8',
      'conversation',
      signal,
      snapshot,
    );
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      text: 'Проверь участок 8',
      conversation_id: 'conversation',
      selection_context: {
        project_id: 'selection-project',
        state_version: 7,
        plan_version: 3,
        zone_ids: ['zone'],
        object_ids: ['tree'],
      },
    });
    expect(fetch.mock.calls[0][1].signal).toBe(signal);
    expect(
      new Headers(fetch.mock.calls[0][1].headers).get('If-Match'),
    ).toBeNull();
  });

  it('attaches fresh context only to an answer, never resume or approval', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(response()));
    vi.stubGlobal('fetch', fetch);
    await api.answerAgentRun(
      'selection-project',
      'run',
      'Используй текущее выделение',
      selection(),
    );
    await api.resumeAgentRun('selection-project', 'run');
    await api.approveAgentRun('selection-project', 'run', 'saved-preview');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      text: 'Используй текущее выделение',
      selection_context: selection(),
    });
    expect(fetch.mock.calls[1][1].body).toBeUndefined();
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({
      preview_ref: 'saved-preview',
    });
  });

  it('does not reuse another project selection and preserves text-only requests', async () => {
    expect(snapshotSelection('other-project', selection())).toBeUndefined();
    const fetch = vi.fn().mockResolvedValue(response());
    vi.stubGlobal('fetch', fetch);
    await api.createAgentRun('other-project', 'Проверь весь проект');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      text: 'Проверь весь проект',
    });
  });
});
