import {
  acknowledgeControlReceipt,
  CONTROL_RECEIPTS_KEY,
  CONTROL_RECEIPTS_LOCK,
  controlIdentity,
  performAgentControl,
  verifiedControlGeometry,
} from '@/features/assistant/model/autonomous/autonomousControl';
import {
  browserControlPrimitives,
  controlFixture,
  controlGeometry,
} from '@/test/controlFixture';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => {
  localStorage.clear();
  browserControlPrimitives();
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});
describe('source-bound map control and durable receipts', () => {
  it('verifies the complete contour including holes and accepts unrelated plan changes', async () => {
    const f = controlFixture();
    f.project.state_version = 20;
    expect(
      await verifiedControlGeometry(f.command, f.proof, f.project),
    ).toEqual(controlGeometry);
  });
  it.each([
    'digest',
    'geometry',
    'project',
    'version',
    'target',
    'attempt',
    'bounds',
  ] as const)(
    'rejects mismatched %s without moving the camera',
    async (mismatch) => {
      const f = controlFixture();
      if (mismatch === 'digest') f.proof.geometry_json += ' ';
      if (mismatch === 'geometry')
        f.project.planting_zones![0].geometry = {
          ...controlGeometry,
          coordinates: [controlGeometry.coordinates[0]],
        };
      if (mismatch === 'project') f.project.id = 'other-project';
      if (mismatch === 'version') f.project.geometry_version = 3;
      if (mismatch === 'target')
        f.project.planting_zones![0].id = 'another-zone';
      if (mismatch === 'attempt')
        f.proof.command = {
          ...f.command,
          execution_attempt_id: 'other-attempt',
        };
      if (mismatch === 'bounds')
        f.proof.command = f.command = { ...f.command, bounds: [0, 0, 1, 1] };
      const camera = vi.fn();
      expect(
        await performAgentControl(
          f.command,
          f.proof,
          f.project,
          camera,
          new AbortController().signal,
        ),
      ).toMatchObject({ status: 'failed', error_code: 'CONTROL_STALE' });
      expect(camera).not.toHaveBeenCalled();
    },
  );
  it('keeps only an outcome receipt and returns it without replaying completed movement', async () => {
    const f = controlFixture();
    const camera = vi.fn(async () => ({ status: 'completed' as const }));
    const first = await performAgentControl(
      f.command,
      f.proof,
      f.project,
      camera,
      new AbortController().signal,
    );
    const second = await performAgentControl(
      f.command,
      f.proof,
      f.project,
      camera,
      new AbortController().signal,
    );
    expect(second).toEqual(first);
    expect(camera).toHaveBeenCalledOnce();
    expect(localStorage.getItem(CONTROL_RECEIPTS_KEY)).not.toContain(
      'coordinates',
    );
    expect(localStorage.getItem(CONTROL_RECEIPTS_KEY)).not.toContain(
      'Восточный',
    );
  });
  it('never replays an interrupted browser session with a started receipt', async () => {
    const f = controlFixture();
    const camera = vi.fn();
    localStorage.setItem(
      CONTROL_RECEIPTS_KEY,
      JSON.stringify([{ identity: controlIdentity(f.command) }]),
    );
    expect(
      await performAgentControl(
        f.command,
        f.proof,
        f.project,
        camera,
        new AbortController().signal,
      ),
    ).toMatchObject({
      status: 'unknown',
      error_code: 'CONTROL_OUTCOME_UNKNOWN',
    });
    expect(camera).not.toHaveBeenCalled();
  });
  it('refuses movement if bounded storage has no acknowledged receipt to evict', async () => {
    const f = controlFixture();
    const camera = vi.fn();
    localStorage.setItem(
      CONTROL_RECEIPTS_KEY,
      JSON.stringify(
        Array.from({ length: 128 }, (_, i) => ({ identity: `pending-${i}` })),
      ),
    );
    expect(
      await performAgentControl(
        f.command,
        f.proof,
        f.project,
        camera,
        new AbortController().signal,
      ),
    ).toMatchObject({ status: 'unknown' });
    expect(camera).not.toHaveBeenCalled();
  });
  it('reports an unavailable map without changing project geometry or selection', async () => {
    const f = controlFixture();
    const before = structuredClone(f.project);
    expect(
      await performAgentControl(
        f.command,
        f.proof,
        f.project,
        undefined,
        new AbortController().signal,
      ),
    ).toMatchObject({ status: 'failed', error_code: 'MAP_UNAVAILABLE' });
    expect(f.project).toEqual(before);
  });
  it('serializes cross-command receipt writes and acknowledgement through the same store lock', async () => {
    const first = controlFixture();
    const second = controlFixture();
    second.command = {
      ...second.command,
      id: 'focus-2',
      run_id: 'another-run',
    };
    second.proof.command = second.command;
    const camera = vi.fn(async () => ({ status: 'completed' as const }));
    await Promise.all(
      [first, second].map((f) =>
        performAgentControl(
          f.command,
          f.proof,
          f.project,
          camera,
          new AbortController().signal,
        ),
      ),
    );
    await Promise.all([
      acknowledgeControlReceipt(first.command),
      acknowledgeControlReceipt(second.command),
    ]);
    const records = JSON.parse(localStorage.getItem(CONTROL_RECEIPTS_KEY)!) as {
      identity: string;
      acknowledged: boolean;
    }[];
    expect(records).toHaveLength(2);
    expect(records.every((item) => item.acknowledged)).toBe(true);
    expect(records.map((item) => item.identity).sort()).toEqual(
      [controlIdentity(first.command), controlIdentity(second.command)].sort(),
    );
    expect(
      vi
        .mocked(navigator.locks.request)
        .mock.calls.filter((call) => call[0] === CONTROL_RECEIPTS_LOCK),
    ).toHaveLength(6);
    await performAgentControl(
      first.command,
      first.proof,
      first.project,
      camera,
      new AbortController().signal,
    );
    expect(camera).toHaveBeenCalledTimes(2);
  });
});
