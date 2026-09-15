import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadCadViewer } from './loadCadViewer';

const constructor = vi.hoisted(() => vi.fn());
vi.mock('dxf-viewer', () => ({
  DxfViewer: class {
    static DefaultOptions = {
      clearColor: { clone: () => ({ setHex: () => ({}) }) },
    };
    constructor(host: HTMLElement, options: unknown) {
      constructor(host, options);
    }
  },
}));
beforeEach(() => vi.clearAllMocks());

describe('SDK source decoder configuration', () => {
  it.each(['utf-8', 'windows-1251', 'shift_jis'])(
    'passes %s to the existing SDK constructor',
    async (encoding) => {
      const host = document.createElement('div');
      await loadCadViewer(host, encoding);
      expect(constructor).toHaveBeenCalledWith(
        host,
        expect.objectContaining({
          fileEncoding: encoding,
          retainParsedDxf: false,
        }),
      );
    },
  );
  it('retains UTF-8 for prepared CAD assets without a native codec', async () => {
    await loadCadViewer(document.createElement('div'));
    expect(constructor).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      expect.objectContaining({ fileEncoding: 'utf-8' }),
    );
  });
});
