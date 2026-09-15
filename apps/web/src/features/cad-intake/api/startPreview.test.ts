import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@green/api-client';
import { startPreview } from './startPreview';
import {
  previewReceipt,
  previewRequestFixture as request,
} from '../test/previewFixtures';

afterEach(() => vi.restoreAllMocks());
describe('CAD preview start receipt', () => {
  it('uses the captured source/contour/version and recovers a lost POST receipt', async () => {
    vi.spyOn(api, 'getLatestOperation')
      .mockResolvedValueOnce(null)
      .mockResolvedValue(previewReceipt);
    const start = vi
      .spyOn(api, 'startCadPreview')
      .mockRejectedValue(new TypeError('offline'));
    expect(await startPreview('project', 7, request)).toEqual(previewReceipt);
    expect(start).toHaveBeenCalledWith('project', request, {
      expectedStateVersion: 7,
    });
  });
  it('does not call an old completed result a fresh receipt', async () => {
    vi.spyOn(api, 'getLatestOperation').mockResolvedValue(previewReceipt);
    vi.spyOn(api, 'startCadPreview').mockRejectedValue(new Error('conflict'));
    await expect(startPreview('project', 7, request)).rejects.toThrow(
      'conflict',
    );
  });
  it.each(['version', 'boundary'] as const)(
    'rejects a recovered receipt for another %s',
    async (difference) => {
      const different = structuredClone(previewReceipt);
      if (difference === 'version') different.project_state_version = 8;
      else
        different.cad_preview!.request.boundary.normalized_sha256 = 'f'.repeat(
          64,
        );
      vi.spyOn(api, 'getLatestOperation')
        .mockResolvedValueOnce(null)
        .mockResolvedValue(different);
      vi.spyOn(api, 'startCadPreview').mockRejectedValue(new Error('offline'));
      await expect(startPreview('project', 7, request)).rejects.toThrow(
        'offline',
      );
    },
  );
});
