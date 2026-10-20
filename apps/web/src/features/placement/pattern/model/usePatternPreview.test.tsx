import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  PatternPreview,
  PatternPreviewRequest,
  Project,
} from '@green/api-client';
import { previewWorkspacePattern } from '@/features/workspace/api/workspacePreviews';
import { usePatternPreview } from './usePatternPreview';
import { buildPatternDraft, createPatternFormDefaults } from './patternForm';

vi.mock('@/features/workspace/api/workspacePreviews', () => ({
  previewWorkspacePattern: vi.fn(),
}));
afterEach(() => {
  vi.resetAllMocks();
  vi.restoreAllMocks();
});
const project: Project = {
  id: 'draft',
  name: 'Черновик',
  status: 'editing',
  state_version: 2,
  geometry_version: 1,
  map_ready: true,
  plan: {
    id: 'plan',
    version: 1,
    planning_horizon_year: 20,
    objects: [],
    issues: [],
  },
};
const request: PatternPreviewRequest = {
  ...buildPatternDraft(createPatternFormDefaults(), {
    mode: 'fill',
    zoneIds: ['west'],
  })!,
  base_plan_version: 1,
};
const preview: PatternPreview = {
  type: 'fill',
  pattern_id: 'first',
  planning_horizon_year: 20,
  accepted_count: 2,
  requested_count: 40,
  generated_count: 40,
  rejected_count: 38,
  capacity_shortfall: 38,
  data_confidence: 'verified',
  skipped: [],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function mount() {
  const onAccepted = vi.fn();
  let key = 'first';
  const hook = renderHook(
    ({ year, draftKey, currentProject }) =>
      usePatternPreview({
        projectId: currentProject.id!,
        project: currentProject,
        tool: 'pattern_fill',
        getYear: () => year,
        draftKey,
        getDraftKey: () => key,
        onAccepted,
      }),
    { initialProps: { year: 0, draftKey: key, currentProject: project } },
  );
  return {
    ...hook,
    onAccepted,
    change: (next: string, year = 0, currentProject = project) => {
      key = next;
      hook.rerender({ year, draftKey: next, currentProject });
    },
    changeWithoutRender: (next: string) => {
      key = next;
    },
  };
}

it('keeps all edits local and checks the selected scenario without replacing the saved plan horizon', async () => {
  const network = vi.spyOn(globalThis, 'fetch');
  const owner = mount();
  owner.change('year10-count24-speciesB', 10);
  expect(previewWorkspacePattern).not.toHaveBeenCalled();
  vi.mocked(previewWorkspacePattern).mockResolvedValue(preview);
  await act(async () => {
    owner.result.current.mutate(request);
    owner.result.current.mutate(request);
  });
  expect(previewWorkspacePattern).toHaveBeenCalledExactlyOnceWith(
    'draft',
    { ...request, scenario_horizon_year: 10, base_plan_version: 1 },
    expect.any(AbortSignal),
    expect.any(Function),
  );
  expect(owner.result.current.data).toEqual(preview);
  expect(owner.result.current.isCurrent()).toBe(true);
  expect(project.plan?.planning_horizon_year).toBe(20);
  expect(project.plan?.version).toBe(1);
  expect(project.state_version).toBe(2);
  expect(network).not.toHaveBeenCalled();
});

it('checks current growth by default even when a legacy plan retained another horizon', async () => {
  const owner = mount();
  vi.mocked(previewWorkspacePattern).mockResolvedValue(preview);
  await act(async () => owner.result.current.mutate(request));
  expect(previewWorkspacePattern).toHaveBeenCalledWith(
    'draft',
    expect.objectContaining({ scenario_horizon_year: 0, base_plan_version: 1 }),
    expect.any(AbortSignal),
    expect.any(Function),
  );
  expect(project.plan?.planning_horizon_year).toBe(20);
});

it('preserves the checked result during editing while rejecting stale application synchronously', async () => {
  const owner = mount();
  vi.mocked(previewWorkspacePattern).mockResolvedValue(preview);
  await act(async () => owner.result.current.mutate(request));
  act(() => owner.result.current.edit());
  expect(owner.result.current.data).toEqual(preview);
  owner.changeWithoutRender('new-count');
  expect(owner.result.current.isCurrent()).toBe(false);
  owner.change('new-count');
  expect(owner.result.current.data).toEqual(preview);
  expect(owner.result.current.editing).toBe(true);
  expect(previewWorkspacePattern).toHaveBeenCalledOnce();
});

it('allows draft changes during a request, aborts it, and ignores late progress and completion', async () => {
  const response = deferred<PatternPreview>();
  vi.mocked(previewWorkspacePattern).mockReturnValueOnce(response.promise);
  const owner = mount();
  await act(async () => owner.result.current.mutate(request));
  const [, , signal, publish] = vi.mocked(previewWorkspacePattern).mock
    .calls[0];
  owner.change('count80');
  expect(signal.aborted).toBe(true);
  expect(owner.result.current.isPending).toBe(false);
  await act(async () => {
    publish?.(preview);
    response.resolve(preview);
  });
  expect(owner.result.current.data).toBeUndefined();
  expect(owner.result.current.progress).toBeUndefined();
  expect(owner.onAccepted).not.toHaveBeenCalled();
});

it('only publishes the newest explicit check, even if an older request completes last', async () => {
  const first = deferred<PatternPreview>(),
    second = deferred<PatternPreview>();
  vi.mocked(previewWorkspacePattern)
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  const owner = mount();
  await act(async () => owner.result.current.mutate(request));
  owner.change('count80');
  await act(async () =>
    owner.result.current.mutate({ ...request, target_count: 80 }),
  );
  const newer = { ...preview, pattern_id: 'newest' };
  await act(async () => second.resolve(newer));
  await act(async () => first.resolve(preview));
  expect(owner.result.current.data).toEqual(newer);
  expect(owner.onAccepted).toHaveBeenCalledExactlyOnceWith(newer);
});

it('cancels a scenario check and ignores its late result', async () => {
  const check = deferred<PatternPreview>();
  const owner = mount();
  vi.mocked(previewWorkspacePattern).mockReturnValue(check.promise);
  owner.change('year10', 10);
  act(() => owner.result.current.mutate(request));
  expect(owner.result.current.phase).toBe('checking');
  const [, submitted, signal] = vi.mocked(previewWorkspacePattern).mock
    .calls[0];
  expect(submitted.scenario_horizon_year).toBe(10);
  act(() => owner.result.current.cancel());
  expect(owner.result.current.phase).toBeUndefined();
  expect(signal.aborted).toBe(true);
  await act(async () => check.resolve(preview));
  expect(owner.result.current.isPending).toBe(false);
  expect(owner.result.current.data).toBeUndefined();
  expect(owner.onAccepted).not.toHaveBeenCalled();
});

it('enters the checking phase immediately without a preceding project write', async () => {
  const check = deferred<PatternPreview>();
  const owner = mount();
  vi.mocked(previewWorkspacePattern).mockReturnValue(check.promise);
  owner.change('year10', 10);
  act(() => owner.result.current.mutate(request));
  expect(owner.result.current.phase).toBe('checking');
  expect(owner.result.current.isPending).toBe(true);
  await act(async () => check.resolve(preview));
  expect(owner.result.current.phase).toBeUndefined();
  expect(owner.result.current.isPending).toBe(false);
});

it('preserves query failures and rejects results from another project revision', async () => {
  const owner = mount();
  vi.mocked(previewWorkspacePattern).mockRejectedValueOnce(
    new Error('Нет ответа'),
  );
  owner.change('year10', 10);
  await act(async () => owner.result.current.mutate(request));
  expect(previewWorkspacePattern).toHaveBeenCalledOnce();
  expect(owner.result.current.error).toBeInstanceOf(Error);
  owner.change('first');
  const response = deferred<PatternPreview>();
  vi.mocked(previewWorkspacePattern).mockReturnValue(response.promise);
  await act(async () => owner.result.current.mutate(request));
  owner.change('first', 20, { ...project, geometry_version: 2 });
  await act(async () => response.resolve(preview));
  expect(owner.result.current.data).toBeUndefined();
  expect(owner.onAccepted).not.toHaveBeenCalled();
});

it('retires a running scenario when an unrelated saved project revision arrives', async () => {
  const checking = deferred<PatternPreview>();
  vi.mocked(previewWorkspacePattern).mockReturnValue(checking.promise);
  const { result, rerender } = renderHook(
    ({ current }) =>
      usePatternPreview({
        projectId: 'draft',
        project: current,
        tool: 'pattern_fill',
        draftKey: 'year10',
        getDraftKey: () => 'year10',
        getYear: () => 10,
        onAccepted: vi.fn(),
      }),
    { initialProps: { current: project } },
  );
  act(() => result.current.mutate(request));
  rerender({
    current: {
      ...project,
      state_version: 9,
      plan: { ...project.plan!, version: 8 },
    },
  });
  expect(vi.mocked(previewWorkspacePattern).mock.calls[0][2].aborted).toBe(
    true,
  );
  await act(async () => checking.resolve(preview));
  expect(previewWorkspacePattern).toHaveBeenCalledOnce();
  expect(result.current.isPending).toBe(false);
  expect(result.current.isCurrent()).toBe(false);
});
