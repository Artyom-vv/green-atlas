import { afterEach, describe, expect, it, vi } from 'vitest';
import { assertAsciiDxf } from './assertAsciiDxf';

afterEach(() => vi.unstubAllGlobals());

function mockResponse(chunks: string[], status = 206) {
  const read = vi.fn().mockImplementation(async () => {
    const text = chunks.shift();
    return text === undefined
      ? { done: true }
      : {
          done: false,
          value: Uint8Array.from(text, (c) => c.charCodeAt(0)),
        };
  });
  const cancel = vi.fn().mockResolvedValue(undefined);
  const fetch = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    body: { getReader: () => ({ read, cancel }), cancel },
  });
  vi.stubGlobal('fetch', fetch);
  return { fetch, read, cancel };
}

describe('CAD ASCII source admission', () => {
  it('rejects a binary signature even when split across network chunks', async () => {
    const { cancel } = mockResponse(['AutoCAD ', 'Binary DXF\r\n\u001a\u0000']);
    await expect(
      assertAsciiDxf('/source', new AbortController().signal),
    ).rejects.toThrow('бинарный');
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('cancels a server ignoring Range after the first ASCII chunk', async () => {
    const { read, cancel, fetch } = mockResponse(
      ['0\nSECTION\n2\nHEADER\n9\n$ACADVER\n', 'large tail'],
      200,
    );
    await assertAsciiDxf('/source', new AbortController().signal);
    expect(read).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][1].headers.Range).toBe('bytes=0-21');
  });

  it('rejects an inaccessible source instead of invoking the CAD worker', async () => {
    mockResponse([], 404);
    await expect(
      assertAsciiDxf('/missing', new AbortController().signal),
    ).rejects.toThrow('404');
  });
});
