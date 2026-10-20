import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiClientError,
  type Project,
  type ProjectRead,
  type ProjectOperation,
} from '@green/api-client';
import { preparationApi } from '../api/preparationApi';
import { mappingKey, type PreparationAttempt } from './preparationRecovery';
import { sourceIdentity } from './sourcePreparation';
import { usePreparationCommit } from './usePreparationCommit';

vi.mock('../api/preparationApi', () => ({
  preparationApi: {
    saveMappings: vi.fn(),
    startGeometryOperation: vi.fn(),
    startCapturePreparation: vi.fn(),
    getProject: vi.fn(),
    getProjectRead: vi.fn(),
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
      mapping_confirmed: true,
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
const projectRead = (version: number): ProjectRead => ({
  ...project(version),
  resume_kind: 'source_preparation',
  prepared_state: 'missing',
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
  const onContinue = vi.fn();
  const hook = renderHook(() =>
    usePreparationCommit({
      source: request().source,
      onProject,
      onOperation,
      onContinue,
    }),
  );
  return { ...hook, onProject, onOperation, onContinue };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(preparationApi.saveMappings).mockResolvedValue(project(2));
  vi.mocked(preparationApi.startGeometryOperation).mockResolvedValue(
    operation(),
  );
  vi.mocked(preparationApi.getProject).mockResolvedValue(project(2));
  vi.mocked(preparationApi.getProjectRead).mockResolvedValue(projectRead(2));
  vi.mocked(preparationApi.getLatestOperation).mockResolvedValue(null);
});

describe('source preparation commit recovery', () => {
  it('automatically reads a lost recognition save and stays busy until matching metadata arrives', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('response lost'),
    );
    let resolveRead!: (value: ProjectRead) => void;
    vi.mocked(preparationApi.getProjectRead).mockImplementation(
      () => new Promise((resolve) => (resolveRead = resolve)),
    );
    const { result, onProject } = openController();
    let completion!: ReturnType<typeof result.current.saveOnly>;
    act(() => {
      completion = result.current.saveOnly(request(), {
        confirmRead: true,
        recoverLostResponse: true,
      });
    });
    await waitFor(() => expect(result.current.phase).toBe('recovering'));
    expect(result.current.isPending).toBe(true);
    expect(onProject).not.toHaveBeenCalled();
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    await act(async () => {
      resolveRead(projectRead(2));
      await completion;
    });
    expect(result.current.phase).toBe('idle');
    expect(result.current.needsRecovery).toBe(false);
    expect(onProject).toHaveBeenCalledExactlyOnceWith(projectRead(2));
    expect(preparationApi.getProjectRead).toHaveBeenCalledExactlyOnceWith(
      'project',
      false,
    );
    expect(preparationApi.getProject).not.toHaveBeenCalled();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
  });

  it('confirms a successful recognition save before publishing completion without an extra project read', async () => {
    let resolveRead!: (value: ProjectRead) => void;
    vi.mocked(preparationApi.getProjectRead).mockImplementation(
      () => new Promise((resolve) => (resolveRead = resolve)),
    );
    const { result, onProject } = openController();
    let completion!: ReturnType<typeof result.current.saveOnly>;
    act(() => {
      completion = result.current.saveOnly(request(), {
        confirmRead: true,
        recoverLostResponse: true,
      });
    });
    await waitFor(() => expect(result.current.phase).toBe('reading'));
    expect(onProject).not.toHaveBeenCalled();
    expect(result.current.writeOutcome).toBe('confirmed');
    await act(async () => {
      resolveRead(projectRead(2));
      await completion;
    });
    expect(result.current.phase).toBe('idle');
    expect(onProject).toHaveBeenCalledExactlyOnceWith(projectRead(2));
    expect(preparationApi.getProjectRead).toHaveBeenCalledOnce();
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
  });

  it('keeps a lost recognition save unresolved when automatic reading returns different mappings', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('response lost'),
    );
    const fresh = projectRead(2);
    fresh.layers![0].mapped_kind = 'building';
    vi.mocked(preparationApi.getProjectRead).mockResolvedValue(fresh);
    const { result, onProject } = openController();
    await act(async () => {
      await expect(
        result.current.saveOnly(request(), {
          confirmRead: true,
          recoverLostResponse: true,
        }),
      ).rejects.toThrow('response lost');
    });
    expect(result.current.phase).toBe('unknown');
    expect(result.current.needsRecovery).toBe(true);
    expect(onProject).not.toHaveBeenCalled();
    expect(preparationApi.getProjectRead).toHaveBeenCalledOnce();
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
  });
  it('recovers a lost mapping-only save with one fresh read and no repeated PUT or CAD operation', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('response lost'),
    );
    const { result, onProject } = openController();
    await act(async () => {
      await expect(result.current.saveOnly(request())).rejects.toThrow(
        'response lost',
      );
    });
    expect(result.current.needsRecovery).toBe(true);
    await act(async () => result.current.recover());
    expect(preparationApi.getProjectRead).toHaveBeenCalledExactlyOnceWith(
      'project',
      false,
    );
    expect(preparationApi.getLatestOperation).not.toHaveBeenCalled();
    expect(onProject).toHaveBeenCalledExactlyOnceWith(projectRead(2));
    expect(result.current.needsRecovery).toBe(false);
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
  });

  it.each(['project', 'source', 'mappings', 'version'] as const)(
    'keeps mapping-only recovery blocked when its single read has another %s',
    async (changed) => {
      vi.mocked(preparationApi.saveMappings).mockRejectedValue(
        new TypeError('response lost'),
      );
      const invalid = projectRead(changed === 'version' ? 1 : 2);
      if (changed === 'project') invalid.id = 'other';
      if (changed === 'source')
        invalid.source_file = { ...invalid.source_file!, name: 'other.dxf' };
      if (changed === 'mappings') invalid.layers![0].mapped_kind = 'building';
      vi.mocked(preparationApi.getProjectRead).mockResolvedValue(invalid);
      const { result, onProject } = openController();
      await act(async () => {
        await expect(result.current.saveOnly(request(2))).rejects.toThrow(
          'response lost',
        );
      });
      await act(async () => result.current.recover());
      expect(result.current.needsRecovery).toBe(true);
      expect(preparationApi.getProjectRead).toHaveBeenCalledExactlyOnceWith(
        'project',
        false,
      );
      expect(onProject).not.toHaveBeenCalled();
      expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
      expect(preparationApi.getLatestOperation).not.toHaveBeenCalled();
      expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    },
  );

  it('saves local settings and opens only after a verified prepared read without CAD', async () => {
    const fresh = {
      ...project(2),
      map_ready: true,
      resume_kind: 'workspace' as const,
      prepared_state: 'available' as const,
    };
    vi.mocked(preparationApi.getProjectRead).mockResolvedValue(fresh);
    const { result, onContinue } = openController();
    act(() => result.current.mutate({ ...request(), mode: 'continue' }));
    await waitFor(() =>
      expect(onContinue).toHaveBeenCalledExactlyOnceWith(fresh),
    );
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    expect(result.current.needsRecovery).toBe(false);
  });

  it.each(['missing', 'corrupt'] as const)(
    'keeps a saved metadata correction out of CAD and editing when its read reports %s',
    async (prepared_state) => {
      const fresh = {
        ...project(2),
        map_ready: true,
        resume_kind: 'workspace' as const,
        prepared_state,
      };
      vi.mocked(preparationApi.getProjectRead).mockResolvedValue(fresh);
      const { result, onContinue } = openController();
      act(() => result.current.mutate({ ...request(), mode: 'continue' }));
      await waitFor(() => expect(result.current.phase).toBe('idle'));
      expect(onContinue).not.toHaveBeenCalled();
      expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
      expect(result.current.acknowledgedDraftKey).toBe(request().draftKey);
      expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
      expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    },
  );

  it('saves local settings in source review without a geometry operation or editor navigation', async () => {
    const fresh = {
      ...project(2),
      map_ready: true,
      source_review: { status: 'pending' as const, issues: [] },
      resume_kind: 'workspace' as const,
      prepared_state: 'available' as const,
    };
    vi.mocked(preparationApi.getProjectRead).mockResolvedValue(fresh);
    const { result, onContinue } = openController();
    act(() => result.current.mutate({ ...request(), mode: 'continue' }));
    await waitFor(() => expect(result.current.phase).toBe('idle'));
    expect(onContinue).not.toHaveBeenCalled();
    expect(result.current.acknowledgedDraftKey).toBe(request().draftKey);
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('recovers a confirmed local save after read failure without resending or starting CAD', async () => {
    const fresh = {
      ...project(2),
      map_ready: true,
      resume_kind: 'workspace' as const,
      prepared_state: 'available' as const,
    };
    vi.mocked(preparationApi.getProjectRead)
      .mockRejectedValueOnce(new TypeError('Read interrupted'))
      .mockResolvedValue(fresh);
    const { result, onContinue } = openController();
    act(() => result.current.mutate({ ...request(), mode: 'continue' }));
    await waitFor(() => expect(result.current.needsRecovery).toBe(true));
    expect(result.current.writeOutcome).toBe('confirmed');
    await act(async () => {
      await result.current.recover();
    });
    expect(onContinue).toHaveBeenCalledExactlyOnceWith(fresh);
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('recovers a lost local save through matching fresh inputs without retrying or starting CAD', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('Lost save response'),
    );
    const fresh = {
      ...project(2),
      map_ready: true,
      resume_kind: 'workspace' as const,
      prepared_state: 'available' as const,
    };
    vi.mocked(preparationApi.getProjectRead).mockResolvedValue(fresh);
    const { result, onContinue } = openController();
    act(() => result.current.mutate({ ...request(), mode: 'continue' }));
    await waitFor(() => expect(result.current.writeOutcome).toBe('unknown'));
    await act(async () => {
      await result.current.recover();
    });
    expect(onContinue).toHaveBeenCalledExactlyOnceWith(fresh);
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
  });

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
  it('resolves a save-only lost response with current settings without starting a worker or replaying the save', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('lost'),
    );
    const { result } = openController();
    await act(async () => {
      await result.current.saveOnly(request()).catch(() => undefined);
    });
    expect(result.current.writeOutcome).toBe('unknown');
    await act(async () => {
      await result.current.recover();
    });
    expect(result.current.writeOutcome).toBe('idle');
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    expect(preparationApi.getLatestOperation).not.toHaveBeenCalled();
  });
  it('keeps a save-only response proven when publication and fresh reading fail', async () => {
    const onProject = vi.fn().mockImplementation(() => {
      throw new Error('publication failed');
    });
    const hook = renderHook(() =>
      usePreparationCommit({
        source: request().source,
        onProject,
        onOperation: vi.fn(),
      }),
    );
    await act(async () => {
      await hook.result.current.saveOnly(request()).catch(() => undefined);
    });
    expect(hook.result.current.writeOutcome).toBe('confirmed');
    vi.mocked(preparationApi.getProjectRead).mockRejectedValue(
      new TypeError('offline'),
    );
    await act(async () => {
      await hook.result.current.recover();
    });
    expect(hook.result.current.writeOutcome).toBe('confirmed');
    expect(hook.result.current.readRecovery).toBe(true);
    expect(hook.result.current.acknowledgedDraftKey).toBe(request().draftKey);
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
  });
  it('permits save-only continuation from matching unchanged settings without claiming a write receipt', async () => {
    vi.mocked(preparationApi.saveMappings).mockRejectedValue(
      new TypeError('response lost'),
    );
    vi.mocked(preparationApi.getProjectRead).mockResolvedValue(projectRead(1));
    const { result } = openController();
    await act(async () => {
      await result.current.saveOnly(request()).catch(() => undefined);
    });
    expect(result.current.writeOutcome).toBe('unknown');
    await act(async () => {
      await result.current.recover();
    });
    expect(result.current.writeOutcome).toBe('idle');
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
  });
});

describe('physical capture preparation', () => {
  const capture = (): ProjectOperation => ({
    ...operation(),
    kind: 'prepare_cad_capture',
    status: 'completed',
  });
  it('starts physical preparation without starting the planting calculation', async () => {
    vi.mocked(preparationApi.startCapturePreparation).mockResolvedValue(
      capture(),
    );
    const { result, onOperation, onContinue } = openController();
    act(() => result.current.mutate({ ...request(), mode: 'capture' }));
    await waitFor(() =>
      expect(onOperation).toHaveBeenCalledExactlyOnceWith(
        capture(),
        request().source,
      ),
    );
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    expect(onContinue).not.toHaveBeenCalled();
  });
  it.each([
    ['lost response', new TypeError('Lost reply')],
    [
      'server internal error',
      new ApiClientError('INTERNAL_ERROR', 'Ошибка сервиса'),
    ],
  ])(
    'recovers %s from the capture journal without a second write or a calculated map',
    async (_label, error) => {
      vi.mocked(preparationApi.startCapturePreparation).mockRejectedValue(
        error,
      );
      vi.mocked(preparationApi.getLatestOperation).mockResolvedValue(capture());
      vi.mocked(preparationApi.getProjectRead).mockResolvedValue({
        ...project(2),
        prepared_state: 'available',
        resume_kind: 'source_preparation',
      });
      const { result, onOperation, onContinue } = openController();
      act(() => result.current.mutate({ ...request(), mode: 'capture' }));
      await waitFor(() => expect(result.current.needsRecovery).toBe(true));
      expect(result.current.writeOutcome).toBe('unknown');
      act(() => result.current.mutate({ ...request(), mode: 'capture' }));
      await act(async () => {
        await result.current.recover();
      });
      expect(result.current.needsRecovery).toBe(false);
      expect(preparationApi.getLatestOperation).toHaveBeenCalledWith(
        'project',
        'prepare_cad_capture',
      );
      expect(preparationApi.startCapturePreparation).toHaveBeenCalledOnce();
      expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
      expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
      expect(onOperation).toHaveBeenCalledWith(capture(), request().source);
      expect(onContinue).not.toHaveBeenCalled();
    },
  );
  it.each([
    ['lost response', new TypeError('Lost reply')],
    [
      'server internal error',
      new ApiClientError('INTERNAL_ERROR', 'Ошибка сервиса'),
    ],
  ])(
    'keeps %s with an absent capture receipt uncertain instead of repeating its start',
    async (_label, error) => {
      vi.mocked(preparationApi.startCapturePreparation).mockRejectedValue(
        error,
      );
      vi.mocked(preparationApi.getProjectRead).mockResolvedValue({
        ...project(2),
        prepared_state: 'missing',
        resume_kind: 'source_preparation',
      });
      const { result } = openController();
      act(() => result.current.mutate({ ...request(), mode: 'capture' }));
      await waitFor(() => expect(result.current.needsRecovery).toBe(true));
      await act(async () => {
        await result.current.recover();
      });
      expect(result.current.needsRecovery).toBe(true);
      expect(result.current.writeOutcome).toBe('unknown');
      act(() => result.current.mutate({ ...request(), mode: 'capture' }));
      expect(preparationApi.getLatestOperation).toHaveBeenCalledWith(
        'project',
        'prepare_cad_capture',
      );
      expect(preparationApi.startCapturePreparation).toHaveBeenCalledOnce();
      expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    },
  );
});

describe('подтверждённая ревизия после своего захвата', () => {
  const captureOperation = (): ProjectOperation => ({
    ...operation(),
    kind: 'prepare_cad_capture',
  });
  const completedCapture = (): ProjectOperation => ({
    ...captureOperation(),
    status: 'completed',
    progress: 100,
  });
  const capturedProject = (): ProjectRead => ({
    ...projectRead(3),
    prepared_state: 'available',
  });
  async function startCapture() {
    vi.mocked(preparationApi.startCapturePreparation).mockResolvedValue(
      captureOperation(),
    );
    const controller = openController();
    act(() =>
      controller.result.current.mutate({ ...request(), mode: 'capture' }),
    );
    await waitFor(() => expect(controller.onOperation).toHaveBeenCalledOnce());
    return controller;
  }

  it('продлевает прежнюю квитанцию и запускает расчёт без повторного PUT или чтения', async () => {
    const controller = await startCapture();
    const fresh = capturedProject();
    expect(
      controller.result.current.confirmCaptureRead(
        fresh,
        completedCapture(),
        request().draftKey,
      ),
    ).toBe(true);
    act(() => controller.result.current.mutate(request(fresh.state_version)));
    await waitFor(() =>
      expect(preparationApi.startGeometryOperation).toHaveBeenCalledOnce(),
    );
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startCapturePreparation).toHaveBeenCalledOnce();
    expect(preparationApi.getProjectRead).not.toHaveBeenCalled();
    expect(preparationApi.getProject).not.toHaveBeenCalled();
    controller.unmount();
  });

  it.each([
    [
      'прежний снимок',
      (fresh: ProjectRead) => ({ ...fresh, state_version: 1 }),
    ],
    ['другой проект', (fresh: ProjectRead) => ({ ...fresh, id: 'other' })],
    [
      'другой исходник',
      (fresh: ProjectRead) => ({
        ...fresh,
        source_file: { ...fresh.source_file!, content_sha256: 'changed' },
      }),
    ],
    [
      'неподготовленный снимок',
      (fresh: ProjectRead) => ({
        ...fresh,
        prepared_state: 'missing' as const,
      }),
    ],
    ['удалённый слой', (fresh: ProjectRead) => ({ ...fresh, layers: [] })],
    [
      'другая роль',
      (fresh: ProjectRead) => ({
        ...fresh,
        layers: [{ ...fresh.layers![0], mapped_kind: 'road' as const }],
      }),
    ],
    [
      'другая видимость',
      (fresh: ProjectRead) => ({
        ...fresh,
        layers: [{ ...fresh.layers![0], visible: false }],
      }),
    ],
    [
      'другое подтверждение',
      (fresh: ProjectRead) => ({
        ...fresh,
        layers: [{ ...fresh.layers![0], mapping_confirmed: false }],
      }),
    ],
    [
      'другая категория',
      (fresh: ProjectRead) => ({
        ...fresh,
        layers: [
          { ...fresh.layers![0], category: 'surface_boundary' as const },
        ],
      }),
    ],
  ])('не принимает чужое или устаревшее чтение: %s', async (_, transform) => {
    const controller = await startCapture();
    expect(
      controller.result.current.confirmCaptureRead(
        transform(capturedProject()),
        completedCapture(),
        request().draftKey,
      ),
    ).toBe(false);
    act(() => controller.result.current.mutate(request(3)));
    await waitFor(() =>
      expect(preparationApi.saveMappings).toHaveBeenCalledTimes(2),
    );
    controller.unmount();
  });

  it.each([
    ['чужая операция', { id: 'other' }],
    ['чужая основа', { project_state_version: 3 }],
    ['операция другого проекта', { project_id: 'other' }],
    ['незавершённый захват', { status: 'running' as const }],
    ['отмена', { status: 'cancelled' as const }],
  ])(
    'не продлевает квитанцию по неподходящей операции: %s',
    async (_, patch) => {
      const controller = await startCapture();
      expect(
        controller.result.current.confirmCaptureRead(
          capturedProject(),
          { ...completedCapture(), ...patch },
          request().draftKey,
        ),
      ).toBe(false);
      act(() => controller.result.current.mutate(request(3)));
      await waitFor(() =>
        expect(preparationApi.saveMappings).toHaveBeenCalledTimes(2),
      );
      controller.unmount();
    },
  );

  it('ручная правка черновика не подтверждается результатом захвата', async () => {
    const controller = await startCapture();
    expect(
      controller.result.current.confirmCaptureRead(
        capturedProject(),
        completedCapture(),
        mappingKey([{ ...mappings[0], visible: false }]),
      ),
    ).toBe(false);
    controller.unmount();
  });

  it('более новая ревизия после подтверждения вновь требует записи', async () => {
    const controller = await startCapture();
    expect(
      controller.result.current.confirmCaptureRead(
        capturedProject(),
        completedCapture(),
        request().draftKey,
      ),
    ).toBe(true);
    act(() => controller.result.current.mutate(request(4)));
    await waitFor(() =>
      expect(preparationApi.saveMappings).toHaveBeenCalledTimes(2),
    );
    controller.unmount();
  });

  it('не подтверждает неизвестный ответ команды и не повторяет её POST', async () => {
    vi.mocked(preparationApi.startCapturePreparation).mockRejectedValue(
      new TypeError('Ответ потерян'),
    );
    const controller = openController();
    act(() =>
      controller.result.current.mutate({ ...request(), mode: 'capture' }),
    );
    await waitFor(() =>
      expect(controller.result.current.needsRecovery).toBe(true),
    );
    expect(
      controller.result.current.confirmCaptureRead(
        capturedProject(),
        completedCapture(),
        request().draftKey,
      ),
    ).toBe(false);
    act(() => controller.result.current.mutate(request(3)));
    expect(preparationApi.saveMappings).toHaveBeenCalledOnce();
    expect(preparationApi.startCapturePreparation).toHaveBeenCalledOnce();
    expect(preparationApi.startGeometryOperation).not.toHaveBeenCalled();
    controller.unmount();
  });
});
