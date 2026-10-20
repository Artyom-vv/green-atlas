import { afterEach, describe, expect, it, vi } from 'vitest';
import { cadApi } from './cad';

afterEach(() => vi.unstubAllGlobals());
describe('CAD transport', () => {
  it('binds full-file metadata and streaming URL to the same encoded operation', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;
    await cadApi.getCadSourceAsset('project #1', 'intake/2', signal);
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(
      '/api/projects/project%20%231/operations/intake%2F2/cad-asset',
    );
    expect(cadApi.cadSourceFileUrl('project #1', 'intake/2')).toBe(
      `${url}/file`,
    );
    expect(init.signal?.aborted).toBe(false);
    expect(new Headers(init.headers).has('If-Match')).toBe(false);
  });
});
