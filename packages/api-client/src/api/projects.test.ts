import { afterEach, expect, it, vi } from 'vitest';
import { projectsApi } from './projects';

afterEach(() => vi.unstubAllGlobals());

it('never serves a cached operation status to the desktop polling loop', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('null'));
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;

  await projectsApi.getLatestOperation(
    'project',
    'prepare_cad_project',
    signal,
  );

  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  expect(new URL(url).pathname).toBe('/api/projects/project/operations/latest');
  expect(init.cache).toBe('no-store');
  expect(init.signal).toBe(signal);
});
