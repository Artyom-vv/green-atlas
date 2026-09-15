import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FrameState } from 'ol/Map';
import { assertAsciiDxf } from './assertAsciiDxf';
import { createCadSourceLayer } from './createCadSourceLayer';
import { createCadWorker, loadCadViewer } from './loadCadViewer';
import type { CadViewer } from './sdkTypes';

vi.mock('./assertAsciiDxf', () => ({ assertAsciiDxf: vi.fn() }));
vi.mock('./loadCadViewer', () => ({
  loadCadViewer: vi.fn(),
  createCadWorker: vi.fn(),
  cadFontUrl: () => '/assets/fonts/NotoSans-Regular.ttf',
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function fixture(fileEncoding?: string) {
  const loading = deferred<void>();
  const canvas = document.createElement('canvas');
  const worker = Object.assign(new EventTarget(), { terminate: vi.fn() });
  const viewer: CadViewer = {
    HasRenderer: () => true,
    GetCanvas: () => canvas,
    GetCamera: () => ({
      left: 0,
      right: 0,
      bottom: 0,
      top: 0,
      zoom: 1,
      position: { set: vi.fn() },
      rotation: { set: vi.fn() },
      updateProjectionMatrix: vi.fn(),
      updateMatrixWorld: vi.fn(),
    }),
    GetOrigin: () => ({ x: 100, y: 200 }),
    GetBounds: () => ({ minX: 0, minY: 0, maxX: 1000, maxY: 2000 }),
    GetLayers: () => [{ name: 'green', displayName: 'green', color: 0xff0000 }],
    GetRenderer: () => ({
      info: { render: { calls: 2 } },
      getContext: vi.fn(),
    }),
    GetScene: () => ({ children: [1] }),
    SetSize: vi.fn(),
    ShowLayers: vi.fn(),
    Render: vi.fn(),
    Destroy: vi.fn(),
    Load: vi.fn((params) => {
      params.workerFactory();
      return loading.promise;
    }),
  };
  vi.mocked(loadCadViewer).mockResolvedValue(viewer);
  vi.mocked(createCadWorker).mockReturnValue(worker as unknown as Worker);
  const onError = vi.fn();
  const onReady = vi.fn();
  const abort = new AbortController();
  const controller = createCadSourceLayer({
    assetUrl: '/source.dxf',
    unitScaleToM: 0.001,
    fileEncoding,
    onError,
    onReady,
    signal: abort.signal,
  });
  return {
    controller,
    viewer,
    loading,
    canvas,
    worker,
    onError,
    onReady,
    abort,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(assertAsciiDxf).mockResolvedValue(undefined);
});

describe('CAD source layer lifecycle', () => {
  it('passes the verified source encoding to SDK initialization', async () => {
    const test = fixture('windows-1251');
    test.loading.resolve();
    await test.controller.ready;
    expect(loadCadViewer).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      'windows-1251',
    );
    test.controller.dispose();
  });
  it('reuses unchanged overlay frames but redraws after CAD layer visibility changes', async () => {
    const test = fixture();
    test.loading.resolve();
    await test.controller.ready;
    const frame = {
      size: [800, 600],
      viewState: { center: [100, 200], resolution: 0.5, rotation: 0 },
    } as FrameState;
    const render = () => test.controller.layer.render(frame, test.canvas);
    render();
    render();
    expect(test.viewer.Render).toHaveBeenCalledOnce();
    test.controller.setLayerVisibility(new Set(['green']));
    render();
    expect(test.viewer.Render).toHaveBeenCalledTimes(2);
    test.controller.setLayerVisibility(new Set(['green']));
    render();
    expect(test.viewer.Render).toHaveBeenCalledTimes(2);
    test.controller.setLayerVisibility(new Set());
    render();
    expect(test.viewer.Render).toHaveBeenCalledTimes(3);
    test.controller.setAppearance('design', { green: 'existing_green' });
    render();
    expect(test.viewer.Render).toHaveBeenCalledTimes(4);
    test.controller.setAppearance('design', { green: 'existing_green' });
    render();
    expect(test.viewer.Render).toHaveBeenCalledTimes(4);
    test.controller.setAppearance('cad', {});
    render();
    expect(test.viewer.Render).toHaveBeenCalledTimes(5);
    expect(test.viewer.Load).toHaveBeenCalledOnce();
    test.controller.dispose();
    render();
    expect(test.viewer.Render).toHaveBeenCalledTimes(5);
  });

  it('settles cancellation during lazy SDK initialization and destroys a late viewer', async () => {
    const importing = deferred<CadViewer>();
    const test = fixture();
    vi.mocked(loadCadViewer).mockReturnValue(importing.promise);
    await vi.waitFor(() => expect(loadCadViewer).toHaveBeenCalledOnce());
    test.controller.dispose();
    await expect(test.controller.ready).resolves.toBeNull();
    importing.resolve(test.viewer);
    await vi.waitFor(() => expect(test.viewer.Destroy).toHaveBeenCalledOnce());
    expect(test.viewer.Load).not.toHaveBeenCalled();
    expect(test.onReady).not.toHaveBeenCalled();
    expect(test.onError).not.toHaveBeenCalled();
  });

  it('terminates a pending worker and never publishes its late completion', async () => {
    const test = fixture();
    await vi.waitFor(() => expect(test.viewer.Load).toHaveBeenCalledOnce());
    test.abort.abort();
    await expect(test.controller.ready).resolves.toBeNull();
    test.loading.resolve();
    await Promise.resolve();
    test.controller.dispose();
    expect(test.worker.terminate).toHaveBeenCalledOnce();
    expect(test.viewer.Destroy).toHaveBeenCalledOnce();
    expect(test.controller.layer.getVisible()).toBe(false);
    expect(test.onReady).not.toHaveBeenCalled();
    expect(test.onError).not.toHaveBeenCalled();
  });

  it('applies copied pre-load visibility as one bulk update, then restores it', async () => {
    const test = fixture();
    const hidden = new Set(['green', 'networks']);
    test.controller.setLayerVisibility(hidden);
    hidden.clear();
    test.loading.resolve();
    await expect(test.controller.ready).resolves.toMatchObject({
      boundsM: [0, 0, 1, 2],
    });
    expect(test.viewer.ShowLayers).toHaveBeenLastCalledWith({
      green: false,
      networks: false,
    });
    test.controller.setLayerVisibility(new Set(['green', 'networks']));
    expect(test.viewer.ShowLayers).toHaveBeenCalledOnce();
    test.controller.setLayerVisibility(new Set());
    expect(test.viewer.ShowLayers).toHaveBeenLastCalledWith({
      green: true,
      networks: true,
    });
    expect(test.canvas.style.pointerEvents).toBe('none');
    expect(test.canvas.tabIndex).toBe(-1);
    expect(test.onReady).toHaveBeenCalledOnce();
    test.controller.dispose();
  });

  it('reports a fatal worker error and frees the renderer', async () => {
    const test = fixture();
    await vi.waitFor(() => expect(test.viewer.Load).toHaveBeenCalledOnce());
    test.worker.dispatchEvent(new Event('messageerror'));
    await expect(test.controller.ready).resolves.toBeNull();
    expect(test.onError).toHaveBeenCalledOnce();
    expect(test.viewer.Destroy).toHaveBeenCalledOnce();
    expect(test.onReady).not.toHaveBeenCalled();
  });

  it('reports context loss once after readiness and removes the stale canvas', async () => {
    const test = fixture();
    test.loading.resolve();
    await test.controller.ready;
    test.canvas.dispatchEvent(new Event('webglcontextlost'));
    test.canvas.dispatchEvent(new Event('webglcontextlost'));
    expect(test.onError).toHaveBeenCalledOnce();
    expect(test.viewer.Destroy).toHaveBeenCalledOnce();
    expect(test.canvas.isConnected).toBe(false);
  });
});
