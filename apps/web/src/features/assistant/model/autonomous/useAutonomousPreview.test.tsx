import { useAutonomousPreview } from '@/features/assistant/model/autonomous/useAutonomousPreview';
import { zonePreviewFixture, zoneRunFixture } from '@/test/zoneChangeFixture';
import {
  api,
  ApiClientError,
  type AgentRun,
  type ChangeSetPreview,
} from '@green/api-client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

function pendingRun(
  status: AgentRun['state']['status'] = 'waiting_approval',
): AgentRun {
  return {
    state: {
      run_id: 'run-1',
      project_id: 'project-1',
      status,
      snapshot_version: 3,
      pending_approval: { preview_ref: 'preview-call' },
      intent: {
        raw_text: 'Посади деревья',
        goal: { operation: 'place' },
        scope_mode: 'explicit',
      },
      candidate_zone_ids: [],
      step: 0,
      tool_calls: [],
      tool_fingerprints: [],
      evidence_refs: [],
      max_steps: 64,
    },
    events: [],
    revision: 1,
    created_at: '',
    updated_at: '',
  };
}

function savedPreview(
  expiresAt = new Date(Date.now() + 60_000).toISOString(),
): ChangeSetPreview {
  return {
    id: 'preview-1',
    digest: 'digest-1',
    base_plan_version: 2,
    can_apply: true,
    additions: [{ id: 'tree-1', kind: 'tree', x: 10, y: 20 }],
    expires_at: expiresAt,
  } as unknown as ChangeSetPreview;
}

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useAutonomousPreview', () => {
  it('uses the typed zone endpoint and ignores a late response after switching to a planting preview', async () => {
    let resolve!: (value: ReturnType<typeof zonePreviewFixture>) => void;
    vi.spyOn(api, 'getAgentRunZonePreview').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const planting = savedPreview();
    vi.spyOn(api, 'getAgentRunPreview').mockResolvedValue(planting);
    const changed = vi.fn();
    const { result, rerender } = renderHook(
      ({ run }) => useAutonomousPreview(run, 'project-1', changed),
      { wrapper: wrapper(), initialProps: { run: zoneRunFixture() } },
    );
    const replacement = pendingRun();
    replacement.state.run_id = 'zone-run';
    replacement.state.pending_approval = {
      kind: 'plantings',
      preview_ref: 'zone-call',
    };
    rerender({ run: replacement });
    await waitFor(() => expect(result.current.preview).toEqual(planting));
    await act(async () => resolve(zonePreviewFixture()));
    expect(result.current.zonePreview).toBeUndefined();
    expect(
      changed.mock.calls.some(([value]) => value?.kind === 'planting_zones'),
    ).toBe(false);
  });

  it('rejects a zone response bound to a different project or source revision', async () => {
    const preview = zonePreviewFixture();
    preview.project_id = 'other';
    vi.spyOn(api, 'getAgentRunZonePreview').mockResolvedValue(preview);
    const changed = vi.fn();
    const { result } = renderHook(
      () => useAutonomousPreview(zoneRunFixture(), 'project-1', changed),
      { wrapper: wrapper() },
    );
    await waitFor(() =>
      expect(result.current.error?.message).toContain('не соответствуют'),
    );
    expect(result.current.preview).toBeUndefined();
    expect(changed.mock.calls.every(([value]) => value === undefined)).toBe(
      true,
    );
  });

  it('does not fall back to planting approval for an unknown proposal kind', () => {
    const run = zoneRunFixture();
    // Deliberately malformed persisted data must never select an approval endpoint.
    Object.assign(run.state.pending_approval!, { kind: 'unknown' });
    const planting = vi.spyOn(api, 'getAgentRunPreview');
    const zones = vi.spyOn(api, 'getAgentRunZonePreview');
    const { result } = renderHook(
      () => useAutonomousPreview(run, 'project-1', vi.fn()),
      { wrapper: wrapper() },
    );
    expect(result.current.error?.message).toContain(
      'Тип предложения недоступен',
    );
    expect(planting).not.toHaveBeenCalled();
    expect(zones).not.toHaveBeenCalled();
  });

  it('forwards the saved geometry with the run and project revision', async () => {
    const preview = savedPreview();
    const load = vi.spyOn(api, 'getAgentRunPreview').mockResolvedValue(preview);
    const changed = vi.fn();
    const { result, unmount } = renderHook(
      () => useAutonomousPreview(pendingRun(), 'project-1', changed),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.preview).toEqual(preview));
    expect(load).toHaveBeenCalledWith('project-1', 'run-1', 'preview-call');
    expect(changed).toHaveBeenLastCalledWith({
      runId: 'run-1',
      projectId: 'project-1',
      stateVersion: 3,
      preview,
    });
    expect(result.current.error).toBeNull();
    unmount();
    expect(changed).toHaveBeenLastCalledWith(undefined);
  });

  it.each([
    'finished',
    'cancelled',
    'failed',
    'waiting_question',
    'running',
    'waiting_job',
  ] as const)('does not expose a preview for %s', (status) => {
    const load = vi.spyOn(api, 'getAgentRunPreview');
    const changed = vi.fn();
    const { result } = renderHook(
      () => useAutonomousPreview(pendingRun(status), 'project-1', changed),
      { wrapper: wrapper() },
    );
    expect(load).not.toHaveBeenCalled();
    expect(result.current.preview).toBeUndefined();
    expect(changed).toHaveBeenLastCalledWith(undefined);
  });

  it('does not request geometry for another project', () => {
    const load = vi.spyOn(api, 'getAgentRunPreview');
    const { result } = renderHook(
      () => useAutonomousPreview(pendingRun(), 'project-2', vi.fn()),
      { wrapper: wrapper() },
    );
    expect(load).not.toHaveBeenCalled();
    expect(result.current.preview).toBeUndefined();
  });

  it('clears displayed geometry when approval ends', async () => {
    vi.spyOn(api, 'getAgentRunPreview').mockResolvedValue(savedPreview());
    const changed = vi.fn();
    const { result, rerender } = renderHook(
      ({ run }) => useAutonomousPreview(run, 'project-1', changed),
      {
        wrapper: wrapper(),
        initialProps: { run: pendingRun() },
      },
    );
    await waitFor(() => expect(result.current.preview).toBeDefined());
    rerender({ run: pendingRun('finished') });
    expect(result.current.preview).toBeUndefined();
    expect(changed).toHaveBeenLastCalledWith(undefined);
  });

  it('reports an unavailable preview without forwarding geometry', async () => {
    const failure = new ApiClientError(
      'PREVIEW_UNAVAILABLE',
      'Предложение недоступно',
    );
    const load = vi.spyOn(api, 'getAgentRunPreview').mockRejectedValue(failure);
    const changed = vi.fn();
    const { result } = renderHook(
      () => useAutonomousPreview(pendingRun(), 'project-1', changed),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.error).toBe(failure));
    expect(result.current.preview).toBeUndefined();
    expect(changed.mock.calls.every(([value]) => value === undefined)).toBe(
      true,
    );
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('ignores geometry that arrives after a project switch', async () => {
    let resolve!: (value: ChangeSetPreview) => void;
    vi.spyOn(api, 'getAgentRunPreview').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const changed = vi.fn();
    const { result, rerender } = renderHook(
      ({ projectId }) => useAutonomousPreview(pendingRun(), projectId, changed),
      {
        wrapper: wrapper(),
        initialProps: { projectId: 'project-1' },
      },
    );
    rerender({ projectId: 'project-2' });
    await act(async () => {
      resolve(savedPreview());
    });
    expect(result.current.preview).toBeUndefined();
    expect(changed.mock.calls.every(([value]) => value === undefined)).toBe(
      true,
    );
  });

  it('does not attach a late previous proposal to a restarted run', async () => {
    let finishOld!: (value: ChangeSetPreview) => void;
    let finishNew!: (value: ChangeSetPreview) => void;
    const load = vi
      .spyOn(api, 'getAgentRunPreview')
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOld = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishNew = resolve;
          }),
      );
    const changed = vi.fn();
    const { result, rerender } = renderHook(
      ({ run }) => useAutonomousPreview(run, 'project-1', changed),
      {
        wrapper: wrapper(),
        initialProps: { run: pendingRun() },
      },
    );
    const restarted = pendingRun();
    restarted.state.pending_approval = { preview_ref: 'new-preview-call' };
    restarted.state.snapshot_version = 4;
    rerender({ run: restarted });
    await act(async () => {
      finishOld(savedPreview());
    });
    expect(result.current.preview).toBeUndefined();
    expect(changed.mock.calls.every(([value]) => value === undefined)).toBe(
      true,
    );
    const replacement = {
      ...savedPreview(),
      id: 'replacement',
      base_plan_version: 3,
    };
    await act(async () => {
      finishNew(replacement);
    });
    await waitFor(() => expect(result.current.preview).toEqual(replacement));
    expect(load).toHaveBeenLastCalledWith(
      'project-1',
      'run-1',
      'new-preview-call',
    );
    expect(changed).toHaveBeenLastCalledWith({
      runId: 'run-1',
      projectId: 'project-1',
      stateVersion: 4,
      preview: replacement,
    });
  });

  it('removes an expired preview without requiring another poll', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-09T18:00:00Z'));
    const preview = savedPreview(new Date(Date.now() + 1000).toISOString());
    vi.spyOn(api, 'getAgentRunPreview').mockResolvedValue(preview);
    const changed = vi.fn();
    const { result } = renderHook(
      () => useAutonomousPreview(pendingRun(), 'project-1', changed),
      { wrapper: wrapper() },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(result.current.preview).toEqual(preview);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.preview).toBeUndefined();
    expect(result.current.error?.message).toContain('устарело');
    expect(changed).toHaveBeenLastCalledWith(undefined);
  });
});
