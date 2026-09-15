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
    expect(init.signal).toBe(signal);
    expect(new Headers(init.headers).has('If-Match')).toBe(false);
  });
  it('encodes relative Unicode paths without turning them into URL structure', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await cadApi.listCadDirectory('официальный', '3-я Парковая/План #1 & сети');
    const url = new URL(fetch.mock.calls[0][0]);
    expect(url.pathname).toContain(encodeURIComponent('официальный'));
    expect(url.searchParams.get('path')).toBe('3-я Парковая/План #1 & сети');
  });
  it('uses the captured If-Match for an intake operation without advancing the project', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await cadApi.startCadIntake(
      'project',
      { root_id: 'official', entry: 'План.dwg', entry_sha256: 'a'.repeat(64) },
      { expectedStateVersion: 7 },
    );
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('If-Match')).toBe('"7"');
    expect(JSON.parse(init.body as string).entry).toBe('План.dwg');
  });
  it('publishes the selected contour with captured version and source fingerprints', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const source = {
      path: 'АПОТ/План #1.dwg',
      source_sha256: 'a'.repeat(64),
      normalized_sha256: 'b'.repeat(64),
    };
    const request = {
      intake_operation_id: 'intake',
      manifest_sha256: 'c'.repeat(64),
      source,
      boundary: { ...source, handle: '14651AD' },
    };
    await cadApi.startCadPreview('project', request, {
      expectedStateVersion: 7,
    });
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(
      '/api/projects/project/operations/cad-preview',
    );
    expect(init.method).toBe('POST');
    expect(new Headers(init.headers).get('If-Match')).toBe('"7"');
    expect(JSON.parse(init.body as string)).toEqual(request);
  });
});
