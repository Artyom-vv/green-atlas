import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  AutomaticPlacementPreview,
  AutomaticPlacementRequest,
} from '@green/api-client';
import {
  previewWorkspaceAutomatic,
  previewWorkspacePattern,
} from '@/features/workspace/api/workspacePreviews';
import { buildPatternDraft } from '@/features/placement/pattern/model/patternForm';
import { projectFixture } from '@/test/projectFixture';
import { useWorkspacePlacementDraft } from './useWorkspacePlacementDraft';

vi.mock('@/features/workspace/api/workspacePreviews', async (original) => ({
  ...(await original<
    typeof import('@/features/workspace/api/workspacePreviews')
  >()),
  previewWorkspaceAutomatic: vi.fn(),
  previewWorkspacePattern: vi.fn(),
}));
afterEach(() => {
  vi.resetAllMocks();
  vi.restoreAllMocks();
});

const request: AutomaticPlacementRequest = {
  base_plan_version: 1,
  zone_id: 'zone-east',
  near: 'area',
  plant_kind: 'auto',
  target_count: 8,
};
const preview: AutomaticPlacementPreview = {
  planning_horizon_year: 20,
  requested: 8,
  found: 0,
  shortfall: 8,
  selected_kind: 'tree',
  selection_basis: 'fixture',
  species_revision_ids: [],
};
function pendingPreview() {
  let resolve!: (value: AutomaticPlacementPreview) => void;
  const promise = new Promise<AutomaticPlacementPreview>((complete) => {
    resolve = complete;
  });
  vi.mocked(previewWorkspaceAutomatic).mockReturnValueOnce(promise);
  return resolve;
}

it('keeps horizon, species and count in the form until checking and retains old additions without apply permission', async () => {
  const network = vi.spyOn(globalThis, 'fetch');
  const project = {
    ...projectFixture(),
    plan: {
      id: 'plan',
      version: 1,
      planning_horizon_year: 20,
      objects: [],
      issues: [],
    },
  };
  const { result } = renderHook(() =>
    useWorkspacePlacementDraft({
      projectId: project.id!,
      project,
      tool: 'pattern_fill',
      mapViewport: { current: null },
      onSuggestionAccepted: vi.fn(),
      onChangesAccepted: vi.fn(),
    }),
  );
  act(() => {
    result.current.patternForm.setValue('planningHorizonYear', 20);
    result.current.patternForm.setValue('targetCount', 24);
    result.current.patternForm.setValue('treeSpeciesId', 'tree@1');
    result.current.setSelectedPatternZoneIds(['zone-east']);
  });
  expect(previewWorkspacePattern).not.toHaveBeenCalled();
  const additions = [{ id: 'pending-tree' }];
  vi.mocked(previewWorkspacePattern).mockResolvedValue({
    ...preview,
    type: 'fill',
    pattern_id: 'checked',
    accepted_count: 1,
    change_set: { id: 'add', can_apply: true, additions },
  } as unknown as Awaited<ReturnType<typeof previewWorkspacePattern>>);
  const draft = buildPatternDraft(result.current.patternForm.getValues(), {
    mode: 'fill',
    zoneIds: ['zone-east'],
  })!;
  await act(async () =>
    result.current.previewPattern.mutate({ ...draft, base_plan_version: 1 }),
  );
  expect(result.current.changePreview?.can_apply).toBe(true);
  expect(network).not.toHaveBeenCalled();
  expect(previewWorkspacePattern).toHaveBeenLastCalledWith(
    project.id,
    expect.objectContaining({
      base_plan_version: 1,
      scenario_horizon_year: 20,
    }),
    expect.any(AbortSignal),
    expect.any(Function),
  );
  expect(project.plan.version).toBe(1);
  act(() => result.current.patternForm.setValue('targetCount', 80));
  expect(result.current.patternPreview?.change_set?.additions).toEqual(
    additions,
  );
  expect(result.current.changePreview?.can_apply).toBe(false);
  expect(result.current.previewPattern.editing).toBe(true);
  expect(previewWorkspacePattern).toHaveBeenCalledOnce();
  act(() => {
    result.current.patternForm.setValue('composition', 'mixed');
    result.current.patternForm.setValue('treeSpeciesId', 'tree@2');
    result.current.patternForm.setValue('shrubSpeciesId', 'shrub@3');
  });
  expect(previewWorkspacePattern).toHaveBeenCalledOnce();
  const mixed = buildPatternDraft(result.current.patternForm.getValues(), {
    mode: 'fill',
    zoneIds: ['zone-east'],
  })!;
  await act(async () =>
    result.current.previewPattern.mutate({ ...mixed, base_plan_version: 1 }),
  );
  expect(previewWorkspacePattern).toHaveBeenLastCalledWith(
    project.id,
    expect.objectContaining({
      composition: 'mixed',
      tree_species_revision_id: 'tree@2',
      shrub_species_revision_id: 'shrub@3',
    }),
    expect.any(AbortSignal),
    expect.any(Function),
  );
  expect(result.current.changePreview?.can_apply).toBe(true);
  act(() => result.current.patternForm.setValue('shrubSpeciesId', 'shrub@4'));
  expect(result.current.changePreview?.can_apply).toBe(false);
  expect(network).not.toHaveBeenCalled();
});

it('starts both placement form scenarios at current growth regardless of the saved legacy horizon', () => {
  const project = projectFixture();
  project.plan = {
    id: 'plan',
    version: 1,
    planning_horizon_year: 40,
    objects: [],
    issues: [],
  };
  const { result, rerender } = renderHook(
    ({ tool }) =>
      useWorkspacePlacementDraft({
        projectId: project.id!,
        project,
        tool,
        mapViewport: { current: null },
        onSuggestionAccepted: vi.fn(),
        onChangesAccepted: vi.fn(),
      }),
    {
      initialProps: { tool: 'pattern_fill' as 'pattern_fill' | 'pattern_row' },
    },
  );
  expect(result.current.patternForm.getValues('planningHorizonYear')).toBe(0);
  act(() => result.current.patternForm.setValue('planningHorizonYear', 5));
  rerender({ tool: 'pattern_row' });
  expect(result.current.patternForm.getValues('planningHorizonYear')).toBe(0);
  rerender({ tool: 'pattern_fill' });
  expect(result.current.patternForm.getValues('planningHorizonYear')).toBe(5);
  expect(project.plan?.planning_horizon_year).toBe(40);
  expect(previewWorkspacePattern).not.toHaveBeenCalled();
});

it('retires previews and transient panels without clearing independent row/forms/zone drafts', async () => {
  const complete = pendingPreview();
  const accepted = vi.fn();
  const project = projectFixture();
  const { result } = renderHook(() =>
    useWorkspacePlacementDraft({
      projectId: 'focus-project',
      project,
      tool: 'pattern_row',
      mapViewport: { current: null },
      onSuggestionAccepted: accepted,
      onChangesAccepted: vi.fn(),
    }),
  );
  const axis = {
    type: 'LineString' as const,
    coordinates: [
      [0, 0],
      [10, 0],
    ],
  };
  act(() => {
    result.current.setRowAxis(axis);
    result.current.setSelectedPatternZoneIds(['zone-east']);
    result.current.patternForm.setValue('targetCount', 19);
    result.current.setRecommendationOpen(true);
    result.current.setSpeciesAssignmentOpen(true);
    result.current.setBrushWidth(24);
    result.current.setBrushStrokes([
      {
        mode: 'add',
        geometry: {
          type: 'LineString',
          coordinates: [
            [0, 0],
            [1, 1],
          ],
        },
      },
    ]);
  });
  await act(async () => result.current.previewAutomatic.mutate(request));
  const signal = vi.mocked(previewWorkspaceAutomatic).mock.calls[0][2];
  act(() => result.current.resetPlacementPreviews());
  expect(signal.aborted).toBe(true);
  expect(result.current.previewAutomatic.isPending).toBe(false);
  expect(result.current.rowAxis).toEqual(axis);
  expect(result.current.selectedPatternZoneIds).toEqual(['zone-east']);
  expect(result.current.patternForm.getValues('targetCount')).toBe(19);
  expect(result.current.brushWidth).toBe(24);
  expect(result.current.brushStrokes).toEqual([]);
  expect(result.current.recommendationOpen).toBe(false);
  expect(result.current.speciesAssignmentOpen).toBe(false);
  await act(async () => complete(preview));
  expect(result.current.automaticPreview).toBeUndefined();
  expect(accepted).not.toHaveBeenCalled();
});

it('rejects late previews from an old project basis and accepts only the current suggestion callback', async () => {
  const completeOld = pendingPreview();
  const accepted = vi.fn(),
    changesAccepted = vi.fn();
  const project = projectFixture();
  const { result, rerender } = renderHook(
    ({ current }) =>
      useWorkspacePlacementDraft({
        projectId: 'focus-project',
        project: current,
        tool: 'select',
        mapViewport: { current: null },
        onSuggestionAccepted: accepted,
        onChangesAccepted: changesAccepted,
      }),
    { initialProps: { current: project } },
  );
  await act(async () => result.current.previewAutomatic.mutate(request));
  const oldSignal = vi.mocked(previewWorkspaceAutomatic).mock.calls[0][2];
  rerender({
    current: { ...project, state_version: project.state_version + 1 },
  });
  expect(oldSignal.aborted).toBe(true);
  await act(async () => completeOld(preview));
  expect(result.current.automaticPreview).toBeUndefined();
  expect(accepted).not.toHaveBeenCalled();
  vi.mocked(previewWorkspaceAutomatic).mockResolvedValueOnce(preview);
  await act(async () => result.current.previewAutomatic.mutate(request));
  expect(result.current.automaticPreview).toEqual(preview);
  expect(accepted).toHaveBeenCalledOnce();
  expect(changesAccepted).not.toHaveBeenCalled();
  expect(result.current.patternSettingsOpen).toBe(false);
});
