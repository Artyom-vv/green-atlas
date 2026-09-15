import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type Project, type ProjectOperation } from '@green/api-client';
import { startIntake } from './startIntake';

const selection = { rootId: 'official', path: 'Улица/План.dwg' };
const sha = 'a'.repeat(64);
const request = {
  root_id: selection.rootId,
  entry: selection.path,
  entry_sha256: sha,
};
function operation(
  overrides: Partial<ProjectOperation> = {},
): ProjectOperation {
  return {
    id: 'op',
    project_id: 'project',
    kind: 'inspect_cad_package',
    status: 'running',
    stage: 'Проверяем комплект',
    project_state_version: 7,
    cad_intake: { request },
    ...overrides,
  } as ProjectOperation;
}
function setup() {
  const project: Project = {
    id: 'project',
    name: 'План',
    state_version: 7,
    status: 'empty',
    map_ready: false,
    geometry_version: 0,
  };
  const fingerprint = vi.spyOn(api, 'fingerprintCadDrawing').mockResolvedValue({
    root_id: 'official',
    path: selection.path,
    sha256: sha,
    bytes: 123,
  });
  const create = vi.spyOn(api, 'createProject').mockResolvedValue(project);
  const read = vi.spyOn(api, 'getProject').mockResolvedValue(project);
  const start = vi.spyOn(api, 'startCadIntake').mockResolvedValue(operation());
  const latest = vi.spyOn(api, 'getLatestOperation').mockResolvedValue(null);
  return { fingerprint, create, read, start, latest };
}
afterEach(() => vi.restoreAllMocks());

describe('CAD intake command', () => {
  it('fingerprints the selected source and pins the command to the read project version', async () => {
    const mocks = setup();
    const resolved = vi.fn();
    await startIntake(selection, 'project', resolved);
    expect(mocks.create).not.toHaveBeenCalled();
    expect(resolved).toHaveBeenCalledWith('project');
    expect(mocks.start).toHaveBeenCalledWith('project', request, {
      expectedStateVersion: 7,
    });
  });
  it('does not create an empty project when the selected file cannot be verified', async () => {
    const mocks = setup();
    mocks.fingerprint.mockRejectedValue(new Error('Файл изменился'));
    await expect(startIntake(selection, undefined, vi.fn())).rejects.toThrow(
      'Файл изменился',
    );
    expect(mocks.create).not.toHaveBeenCalled();
  });
  it('recovers a lost POST receipt only from the same source and project basis', async () => {
    const mocks = setup();
    mocks.start.mockRejectedValue(new Error('Связь потеряна'));
    mocks.latest.mockResolvedValue(operation());
    await expect(startIntake(selection, 'project', vi.fn())).resolves.toEqual({
      projectId: 'project',
      operation: operation(),
    });
    expect(mocks.start).toHaveBeenCalledOnce();
  });
  it('does not treat the old completed passport as a receipt for a fresh check', async () => {
    const mocks = setup();
    mocks.latest.mockResolvedValue(operation({ status: 'completed' }));
    mocks.start.mockRejectedValue(new Error('Новый запуск не принят'));
    await expect(startIntake(selection, 'project', vi.fn())).rejects.toThrow(
      'Новый запуск не принят',
    );
  });
  it.each([
    { project_state_version: 8 },
    { cad_intake: { request: { ...request, entry_sha256: 'b'.repeat(64) } } },
    { status: 'failed' as const },
  ])(
    'does not mistake another or failed operation for its receipt: %o',
    async (other) => {
      const mocks = setup();
      mocks.start.mockRejectedValue(new Error('Запуск не подтверждён'));
      mocks.latest.mockResolvedValue(operation(other));
      await expect(startIntake(selection, 'project', vi.fn())).rejects.toThrow(
        'Запуск не подтверждён',
      );
    },
  );
});
