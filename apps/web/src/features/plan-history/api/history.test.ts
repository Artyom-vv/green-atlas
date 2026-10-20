import { afterEach, describe, expect, it, vi } from 'vitest';
import { readHistorySnapshot } from './history';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('history snapshot transport', () => {
  it('reads the plan and revision around history without requesting map geometry', async () => {
    const project = {
      id: 'compact-history',
      state_version: 12,
      geometry: null,
      source_geometry: null,
      geometry_version: 5,
      plan: { id: 'plan-1', version: 8, objects: [{ id: 'plant-1' }] },
    };
    const history = {
      can_undo: true,
      can_redo: false,
      cursor_ordinal: 3,
      undo_target_id: 'change-3',
      redo_target_id: null,
      entries: [],
    };
    const replies = [project, history, project];
    const fetch = vi
      .fn()
      .mockImplementation(
        async () => new Response(JSON.stringify(replies.shift())),
      );
    vi.stubGlobal('fetch', fetch);

    await expect(readHistorySnapshot(project.id)).resolves.toEqual({
      project,
      history,
    });

    expect(fetch).toHaveBeenCalledTimes(3);
    const requests = fetch.mock.calls.map(([url, init]) => ({
      address: new URL(url),
      init: init as RequestInit,
    }));
    expect(requests.map(({ address }) => address.pathname)).toEqual([
      '/api/projects/compact-history',
      '/api/projects/compact-history/plan/history',
      '/api/projects/compact-history',
    ]);
    for (const { address, init } of [requests[0], requests[2]]) {
      expect(address.searchParams.get('include_geometry')).toBe('false');
      expect(init.cache).toBe('no-store');
    }
    expect(requests.every(({ init }) => (init.method ?? 'GET') === 'GET')).toBe(
      true,
    );
  });

  it.each([
    ['revision', 'revision-history', 'revision-history', 12, 13],
    ['first owner', 'another-project', 'first-owner-history', 12, 12],
    ['last owner', 'last-owner-history', 'another-project', 12, 12],
  ] as const)(
    'rejects a compact snapshot with a different %s',
    async (kind, firstId, lastId, firstVersion, lastVersion) => {
      const id = `${kind.replaceAll(' ', '-')}-history`;
      const replies = [
        { id: firstId, state_version: firstVersion },
        { entries: [] },
        { id: lastId, state_version: lastVersion },
      ];
      const fetch = vi
        .fn()
        .mockImplementation(
          async () => new Response(JSON.stringify(replies.shift())),
        );
      vi.stubGlobal('fetch', fetch);

      await expect(readHistorySnapshot(id)).rejects.toThrow(
        'Проект изменился во время проверки истории',
      );
      expect(fetch).toHaveBeenCalledTimes(3);
      expect(
        fetch.mock.calls.every(([, init]) => (init.method ?? 'GET') === 'GET'),
      ).toBe(true);
    },
  );
});
