import { afterEach, expect, it, vi } from 'vitest';
import {
  api,
  type CadPrepareRequest,
  type ProjectOperation,
} from '@green/api-client';
import { startPrepare } from './startPrepare';

afterEach(() => vi.restoreAllMocks());

const request: CadPrepareRequest = {
  profile_version: 1,
  intake_operation_id: 'intake',
  manifest_sha256: 'a'.repeat(64),
};

it('recovers a receipt with server-defaulted opening decisions', async () => {
  const receipt = {
    id: 'prepare',
    project_id: 'project',
    project_state_version: 1,
    kind: 'prepare_cad_project',
    status: 'running',
    progress: 0,
    progress_mode: 'indeterminate',
    stage: 'Подготовка',
    cad_prepare: {
      request: {
        ...request,
        opening_review: {
          skipped_drawings: [],
          skipped_references: [],
          accept_partial_geometry: false,
        },
      },
    },
  } as ProjectOperation;
  vi.spyOn(api, 'getLatestOperation')
    .mockResolvedValueOnce(null)
    .mockResolvedValue(receipt);
  vi.spyOn(api, 'startCadPrepare').mockRejectedValue(new TypeError('offline'));
  expect(await startPrepare('project', 1, request)).toEqual(receipt);
});

it('does not recover another users decision to accept partial geometry', async () => {
  const receipt = {
    id: 'prepare',
    project_id: 'project',
    project_state_version: 1,
    kind: 'prepare_cad_project',
    status: 'running',
    progress: 0,
    progress_mode: 'indeterminate',
    stage: 'Подготовка',
    cad_prepare: {
      request: {
        ...request,
        opening_review: { accept_partial_geometry: true },
      },
    },
  } as ProjectOperation;
  vi.spyOn(api, 'getLatestOperation')
    .mockResolvedValueOnce(null)
    .mockResolvedValue(receipt);
  vi.spyOn(api, 'startCadPrepare').mockRejectedValue(new TypeError('offline'));
  await expect(startPrepare('project', 1, request)).rejects.toThrow('offline');
});
