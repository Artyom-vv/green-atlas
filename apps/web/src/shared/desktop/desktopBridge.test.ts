import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  localRuntimeAvailable,
  setLocalRuntimeAvailable,
} from '@green/api-client';
import { createDesktopBridge } from './desktopBridge';
import { parseDesktopMessage } from './protocol';

const origin = 'http://127.0.0.1:8020';
const capability = {
  schemaVersion: 1,
  type: 'capabilities',
  documentGeneration: 2,
  origin,
};
const request = {
  ...capability,
  type: 'navigationRequest',
  requestId: 1,
  action: 'close',
};
afterEach(() => setLocalRuntimeAvailable(true));
function fixture(pathname = '/projects', restored = true) {
  const postMessage = vi.fn();
  const host = {
    location: { origin, pathname },
    webkit: { messageHandlers: { greenAtlasDesktop: { postMessage } } },
  } as unknown as Parameters<typeof createDesktopBridge>[0];
  const bridge = createDesktopBridge(host);
  const stop = bridge.start();
  const receive = (value: unknown) => host.__greenAtlasDesktopReceive?.(value);
  receive(capability);
  if (restored) receive({ ...capability, type: 'runtimeRestored' });
  return { bridge, receive, postMessage, stop };
}

describe('маршрут непрерывного открытия из плагина', () => {
  const target =
    '/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/setup?open=editor';
  it('передаёт весь route с intent владельцу проверки ухода', () => {
    const state = fixture(
      '/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/workspace',
    );
    const handle = vi.fn();
    state.bridge.register(handle);
    state.receive({ ...request, action: 'handoff', target });
    expect(handle).toHaveBeenCalledExactlyOnceWith({
      ...request,
      action: 'handoff',
      target,
    });
    state.stop();
  });
  it.each(['&redirect=https://example.com', '#fragment', '\n', '&open=other'])(
    'отклоняет добавление %s к разрешённому intent',
    (suffix) =>
      expect(
        parseDesktopMessage(
          { ...request, action: 'handoff', target: target + suffix },
          origin,
        ),
      ).toBeUndefined(),
  );
});
describe('desktop document bridge', () => {
  it('keeps a native initial load paused until owned restoration rather than capabilities alone', () => {
    const f = fixture('/projects', false);
    expect(localRuntimeAvailable()).toBe(false);
    f.receive({ ...capability, type: 'runtimeRestored' });
    expect(localRuntimeAvailable()).toBe(true);
    f.stop();
  });
  it('accepts runtime ownership changes only for the current document and cancels its pending acknowledgement', () => {
    const f = fixture();
    const handler = vi.fn();
    f.bridge.register(handler);
    f.receive(request);
    f.receive({
      ...capability,
      type: 'runtimeUnavailable',
      documentGeneration: 1,
    });
    f.receive({
      ...capability,
      type: 'runtimeUnavailable',
      origin: 'http://localhost:9999',
    });
    expect(localRuntimeAvailable()).toBe(true);
    f.receive({ ...capability, type: 'runtimeUnavailable' });
    expect(localRuntimeAvailable()).toBe(false);
    f.receive({ ...capability, type: 'runtimeRestored' });
    expect(localRuntimeAvailable()).toBe(true);
    f.bridge.decision(request as Parameters<typeof f.bridge.decision>[0], true);
    expect(
      f.postMessage.mock.calls.filter(([value]) => value.type === 'decision'),
    ).toHaveLength(0);
    f.receive({ ...request, requestId: 2 });
    expect(handler).toHaveBeenCalledTimes(2);
    f.stop();
  });
  it('rejects foreign origins, bad generations and an external handoff target', () => {
    expect(
      parseDesktopMessage(
        { ...request, origin: 'https://example.com' },
        origin,
      ),
    ).toBeUndefined();
    expect(
      parseDesktopMessage({ ...request, documentGeneration: 0 }, origin),
    ).toBeUndefined();
    expect(
      parseDesktopMessage(
        { ...request, action: 'handoff', target: '//example.com/projects' },
        origin,
      ),
    ).toBeUndefined();
  });
  it('requires capabilities and ignores a stale document, duplicate request and old acknowledgement', () => {
    const f = fixture();
    const handler = vi.fn();
    f.bridge.register(handler);
    f.receive({ ...request, documentGeneration: 1 });
    f.receive(request);
    f.receive(request);
    expect(handler).toHaveBeenCalledOnce();
    f.receive({ ...capability, documentGeneration: 3 });
    f.bridge.decision(request as Parameters<typeof f.bridge.decision>[0], true);
    expect(
      f.postMessage.mock.calls.filter(([value]) => value.type === 'decision'),
    ).toHaveLength(0);
    f.stop();
  });
  it('fails closed while a workspace guard is mounting and allows closing the library', () => {
    const loadingWorkspace = fixture('/projects/a/workspace');
    loadingWorkspace.receive(request);
    expect(loadingWorkspace.postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'decision', allow: false }),
    );
    loadingWorkspace.stop();
    const library = fixture();
    library.receive(request);
    expect(library.postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'decision', allow: true }),
    );
    library.stop();
  });
});
