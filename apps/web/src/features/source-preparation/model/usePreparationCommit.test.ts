import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project, ProjectOperation } from '@green/api-client';
import { preparationApi } from '../api/preparationApi';
import { mappingKey, type PreparationAttempt } from './preparationRecovery';
import { sourceIdentity } from './sourcePreparation';
import { usePreparationCommit } from './usePreparationCommit';

vi.mock('../api/preparationApi', () => ({
  preparationApi: {
    saveMappings: vi.fn(),
    startGeometryOperation: vi.fn(),
    getProject: vi.fn(),
    getLatestOperation: vi.fn(),
  },
}));

const mappings = [
  { layer_id: 'site', kind: 'site_border' as const, visible: true },
];
const project = (version: number): Project => ({
  id: 'project',
  name: 'Проект',
  status: 'mapped',
  state_version: version,
  geometry_version: 1,
  map_ready: false,
  source_file: {
    accept_partial_geometry: false,
    name: 'site.dxf',
    size: 10,
    imported_at: '2026-09-14T10:00:00Z',
    dxf_version: 'AC1027',
    units: 'm',
    units_assumed: false,
    entity_count: 1,
  },
  layers: [
    {
      id: 'site',
      source_name: 'SITE',
      mapped_kind: 'site_border',
      suggested_kind: 'site_border',
      visible: true,
      color: '#000',
      object_count: 1,
      linetype: 'CONTINUOUS',
      geometry_complete: true,
      required: true,
    },
  ],
});
const operation = (basis = 2): ProjectOperation => ({
  id: 'operation',
  project_id: 'project',
  kind: 'calculate_geometry',
  status: 'running',
  project_state_version: basis,
  progress: 10,
  progress_mode: 'determinate',
  stage: 'Расчёт',
});
const request = (baseStateVersion = 1): PreparationAttempt => ({
  projectId: 'project',
  source: sourceIdentity(project(1)),
  mappings,
  draftKey: mappingKey(mappings),
  baseStateVersion,
});
function openController() {
  const onProject = vi.fn();
  const onOperation = vi.fn();
  const hook = renderHook(() =>
    usePreparationCommit({
      source: request().source,
      onProject,
      onOperation,
    }),
  );
  return { ...hook, onProject, onOperation };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(preparationApi.saveMappings).mockResolvedValue(project(2));
  vi.mocked(preparationApi.startGeometryOperation).mockResolvedValue(
    operation(),
  );
  vi.mocked(preparationApi.getProject).mockResolvedValue(project(2));
  vi.mocked(preparationApi.getLatestOperation).mockResolvedValue(null);
});

describe('source preparation commit recovery', () => {
  it('recovers a lost start response through the operation basis without another write', async () => {
    vi.mocked(preparationApi.startGeometryOperation).mockRejectedValue(
      new TypeError('offline'),
    );
    vi.mocked(preparationApi.getLatestOperation).mockResolvedValue(operation());
    const { result, onOperation } = openController();
    act(() => {
      result.current.mutate(request());
      result.current.mutate(request());
    });
    await waitFor(() => expect(result.current.needsRecovery).toBe(true));
    act(() => result.current.mutate(request()));
    await act(async () => {
      await result.current.recover();
    });
    expect(onOperation).toHaveBeenCalledExactlyOnceWith(
      operation(),
      request().source,
    );
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).toHaveBeenCalledOnce();
    expect(preparationApi.getProject).toHaveBeenCalledTimes(2);
    expect(result.current.needsRecovery).toBe(false);
  });

  it('only retries start after a stable read proves the saved mapping version', async () => {
    vi.mocked(preparationApi.startGeometryOperation)
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(operation());
    const { result } = openController();
    act(() => result.current.mutate(request()));
    await waitFor(() => expect(result.current.needsRecovery).toBe(true));
    await act(async () => {
      await result.current.recover();
    });
    expect(preparationApi.startGeometryOperation).toHaveBeenCalledOnce();
    act(() => result.current.mutate(request(2)));
    await waitFor(() => expect(result.current.phase).toBe('idle'));
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).toHaveBeenCalledTimes(2);
  });

  it('adopts matching current settings after a lost save, without claiming a save receipt', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('response lost'),
    );
    const { result, onProject } = openController();
    act(() => result.current.mutate(request()));
    await waitFor(() => expect(result.current.needsRecovery).toBe(true));
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.recover();
    });
    expect(onProject).toHaveBeenCalledWith(project(2));
    act(() => result.current.mutate(request(2)));
    await waitFor(() => expect(result.current.phase).toBe('idle'));
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).toHaveBeenCalledOnce();
  });

  it('keeps an unconfirmed save blocked when reading the original version', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('offline'),
    );
    vi.mocked(preparationApi.getProject).mockResolvedValue(project(1));
    const { result } = openController();
    act(() => result.current.mutate(request()));
    await waitFor(() => expect(result.current.needsRecovery).toBe(true));
    await act(async () => {
      await result.current.recover();
    });
    expect(result.current.message).toContain(
      'Не удалось подтвердить сохранение',
    );
    act(() => result.current.mutate(request()));
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
  });

  it.each([
    'changing version',
    'another basis',
    'another source',
    'read failure',
  ])('%s cannot release the write gate', async (scenario) => {
    vi.mocked(preparationApi.startGeometryOperation).mockRejectedValue(
      new TypeError('offline'),
    );
    if (scenario === 'changing version')
      vi.mocked(preparationApi.getProject)
        .mockResolvedValueOnce(project(2))
        .mockResolvedValueOnce(project(3));
    if (scenario === 'another basis')
      vi.mocked(preparationApi.getLatestOperation).mockResolvedValue(
        operation(1),
      );
    if (scenario === 'another source')
      vi.mocked(preparationApi.getProject).mockResolvedValue({
        ...project(2),
        source_file: {
          ...project(2).source_file!,
          imported_at: '2026-09-14T12:00:00Z',
        },
      });
    if (scenario === 'read failure')
      vi.mocked(preparationApi.getLatestOperation).mockRejectedValue(
        new TypeError('offline'),
      );
    const { result, onOperation } = openController();
    act(() => result.current.mutate(request()));
    await waitFor(() => expect(result.current.needsRecovery).toBe(true));
    await act(async () => {
      await result.current.recover();
    });
    expect(result.current.needsRecovery).toBe(true);
    act(() => result.current.mutate(request(2)));
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).toHaveBeenCalledOnce();
    expect(onOperation).not.toHaveBeenCalled();
  });

  it('accepts a completed matching operation only with the prepared fresh geometry', async () => {
    vi.mocked(preparationApi.startGeometryOperation).mockRejectedValue(
      new TypeError('offline'),
    );
    const completed = { ...operation(), status: 'completed' as const };
    vi.mocked(preparationApi.getLatestOperation).mockResolvedValue(completed);
    vi.mocked(preparationApi.getProject).mockResolvedValue({
      ...project(3),
      map_ready: true,
      geometry_version: 2,
    });
    const { result, onOperation } = openController();
    act(() => result.current.mutate(request()));
    await waitFor(() => expect(result.current.needsRecovery).toBe(true));
    await act(async () => {
      await result.current.recover();
    });
    expect(result.current.needsRecovery).toBe(false);
    expect(onOperation).toHaveBeenCalledExactlyOnceWith(
      completed,
      request().source,
    );
    expect(preparationApi.startGeometryOperation).toHaveBeenCalledOnce();
  });
});
