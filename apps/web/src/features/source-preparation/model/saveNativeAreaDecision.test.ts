import { describe, expect, it, vi } from 'vitest';
import type { Project } from '@green/api-client';
import { saveNativeAreaDecision } from './saveNativeAreaDecision';

const input = {
  source_sha256: 'a'.repeat(64),
  proposal_id: 'area/1',
  proposal_sha256: 'b'.repeat(64),
  decision: 'accepted' as const,
};
const saved = {
  id: 'project',
  state_version: 3,
  source_file: {
    content_sha256: input.source_sha256,
    native_area_proposals: [
      {
        id: input.proposal_id,
        proposal_sha256: input.proposal_sha256,
        decision: 'accepted',
      },
    ],
  },
} as Project;

describe('native decision receipt recovery', () => {
  it('recovers an applied decision by reading, without a second write', async () => {
    const port = {
      decideNativeArea: vi
        .fn()
        .mockRejectedValue(new TypeError('Failed to fetch')),
      getProject: vi.fn().mockResolvedValue(saved),
    };
    expect(await saveNativeAreaDecision('project', input, 2, port)).toBe(saved);
    expect(port.decideNativeArea).toHaveBeenCalledTimes(1);
    expect(port.getProject).toHaveBeenCalledWith('project', false);
  });
  it.each([
    { ...saved, state_version: 2 },
    {
      ...saved,
      source_file: { ...saved.source_file!, content_sha256: 'c'.repeat(64) },
    },
    {
      ...saved,
      source_file: { ...saved.source_file!, native_area_proposals: [] },
    },
  ])(
    'does not claim success without a matching fresh receipt',
    async (current) => {
      const error = new TypeError('Failed to fetch');
      const port = {
        decideNativeArea: vi.fn().mockRejectedValue(error),
        getProject: vi.fn().mockResolvedValue(current),
      };
      await expect(
        saveNativeAreaDecision('project', input, 2, port),
      ).rejects.toBe(error);
      expect(port.decideNativeArea).toHaveBeenCalledTimes(1);
    },
  );
});
