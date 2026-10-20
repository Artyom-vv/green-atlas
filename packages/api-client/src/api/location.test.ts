import { afterEach, expect, it, vi } from 'vitest';
import { locationApi } from './location';

afterEach(() => vi.unstubAllGlobals());
const suggestion = {
  id: 'street',
  label: 'Москва, Парковая',
  kind: 'street' as const,
};
const draft = {
  id: 'draft',
  source_sha256: 'a'.repeat(64),
  geometry_version: 3,
  suggestion,
  status: 'ready',
  message: 'Готово',
};

it('sends address requests to the encoded local project endpoint and supplies the corridor default', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(draft)));
  vi.stubGlobal('fetch', fetch);
  const result = await locationApi.resolveProjectLocation('project #1', {
    suggestion,
  });
  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  expect(new URL(url).pathname).toBe(
    '/api/projects/project%20%231/location/resolve',
  );
  expect(JSON.parse(String(init.body))).toEqual({ buffer_m: 120, suggestion });
  expect(new Headers(init.headers).has('Authorization')).toBe(false);
  expect(result.map_buildings).toEqual({
    type: 'FeatureCollection',
    features: [],
  });
  expect(result.street_candidates).toEqual([]);
});

it('rejects malformed map collections instead of making them available for matching', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            ...draft,
            map_buildings: { type: 'Polygon', coordinates: [] },
          }),
        ),
      ),
  );
  await expect(
    locationApi.resolveProjectLocation('project', { suggestion }),
  ).rejects.toMatchObject({ code: 'LOCATION_GEOMETRY_INVALID' });
});

it('preserves nullable applied source fields from legacy frames', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            configured: true,
            applied: {
              status: 'declared',
              source_sha256: null,
              geometry_version: null,
            },
          }),
        ),
      ),
  );
  const state = await locationApi.getProjectLocation('project');
  expect(state.applied?.source_sha256).toBeNull();
  expect(state.draft).toBeNull();
  expect(state.alignment).toBeNull();
});
