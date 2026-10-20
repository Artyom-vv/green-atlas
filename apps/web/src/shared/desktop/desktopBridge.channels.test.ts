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
function makeFixture(
  pathname: string,
  restored: boolean,
  transport: 'WKWebView' | 'WebView2',
) {
  const postMessage = vi.fn();
  const listeners = new Set<(event: { data: unknown }) => void>();
  const webview = {
    postMessage,
    addEventListener: vi.fn(
      (_type: string, listener: (event: { data: unknown }) => void) =>
        listeners.add(listener),
    ),
    removeEventListener: vi.fn(
      (_type: string, listener: (event: { data: unknown }) => void) =>
        listeners.delete(listener),
    ),
  };
  const host = {
    setTimeout: window.setTimeout.bind(window),
    clearTimeout: window.clearTimeout.bind(window),
    location: { origin, pathname },
    ...(transport === 'WKWebView'
      ? { webkit: { messageHandlers: { greenAtlasDesktop: { postMessage } } } }
      : { chrome: { webview } }),
  } as unknown as Parameters<typeof createDesktopBridge>[0];
  const bridge = createDesktopBridge(host);
  const stop = bridge.start();
  const receive = (value: unknown) => {
    if (transport === 'WKWebView') host.__greenAtlasDesktopReceive?.(value);
    else listeners.forEach((listener) => listener({ data: value }));
  };
  receive(capability);
  if (restored) receive({ ...capability, type: 'runtimeRestored' });
  return { bridge, receive, postMessage, stop, host, webview, listeners };
}

it('correlates the native component picker with the current document, field and request', async () => {
  const f = makeFixture('/projects', true, 'WebView2');
  const pending = f.bridge.chooseDependency('blender');
  const selected = f.postMessage.mock.calls.at(-1)?.[0];
  expect(selected.type).toBe('chooseDependency');
  const completed = vi.fn();
  void pending.then(completed);
  f.receive({
    ...selected,
    type: 'dependencyChosen',
    documentGeneration: 1,
    path: 'D:\\stale.exe',
  });
  f.receive({
    ...selected,
    type: 'dependencyChosen',
    field: 'pbr_root',
    path: 'D:\\wrong.exe',
  });
  await Promise.resolve();
  expect(completed).not.toHaveBeenCalled();
  f.receive({
    ...selected,
    type: 'dependencyChosen',
    path: 'D:\\Мой Blender\\blender.exe',
  });
  expect(await pending).toBe('D:\\Мой Blender\\blender.exe');
  const abandoned = f.bridge.chooseDependency('botaniq_root');
  f.stop();
  expect(await abandoned).toBeUndefined();
});
describe.each(['WKWebView', 'WebView2'] as const)(
  '%s document bridge',
  (transport) => {
    const fixture = (pathname = '/projects', restored = true) =>
      makeFixture(pathname, restored, transport);
    it('removes the owned receiver and rejects a queued event after teardown', () => {
      const f = fixture();
      const queuedCallback = f.host.__greenAtlasDesktopReceive;
      const late =
        transport === 'WebView2'
          ? [...f.listeners][0]
          : (event: { data: unknown }) => queuedCallback?.(event.data);
      f.stop();
      expect(f.host.__greenAtlasDesktopReceive).toBeUndefined();
      expect(f.listeners.size).toBe(0);
      if (transport === 'WebView2') {
        expect(f.webview.removeEventListener).toHaveBeenCalledWith(
          'message',
          late,
        );
      }
      late({ data: { ...capability, documentGeneration: 3 } });
      late({
        data: {
          ...capability,
          documentGeneration: 3,
          type: 'runtimeUnavailable',
        },
      });
      expect(localRuntimeAvailable()).toBe(true);
      f.bridge.updateState({
        hasDraft: true,
        writeOutcome: 'unknown',
        readRecovery: true,
        editingBlocked: true,
      });
      expect(
        f.postMessage.mock.calls.filter(
          ([value]) => value.documentGeneration === 3,
        ),
      ).toHaveLength(0);
    });
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
      f.bridge.decision(
        request as Parameters<typeof f.bridge.decision>[0],
        true,
      );
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
      f.receive(null);
      f.receive(JSON.stringify(request));
      f.receive({ ...request, origin: 'http://localhost:8020' });
      f.receive({ ...request, documentGeneration: 1 });
      f.receive(request);
      f.receive(request);
      expect(handler).toHaveBeenCalledOnce();
      f.receive({ ...capability, documentGeneration: 3 });
      f.bridge.decision(
        request as Parameters<typeof f.bridge.decision>[0],
        true,
      );
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
  },
);

it('subscribes to WebView2 before ready so an immediate host handshake is preserved', () => {
  const listeners = new Set<(event: { data: unknown }) => void>();
  const postMessage = vi.fn((value: { type: string }) => {
    if (value.type !== 'ready') return;
    expect(listeners.size).toBe(1);
    for (const data of [capability, { ...capability, type: 'runtimeRestored' }])
      listeners.forEach((listener) => listener({ data }));
  });
  const host = {
    location: { origin, pathname: '/projects' },
    chrome: {
      webview: {
        postMessage,
        addEventListener: (
          _type: string,
          listener: (event: { data: unknown }) => void,
        ) => listeners.add(listener),
        removeEventListener: (
          _type: string,
          listener: (event: { data: unknown }) => void,
        ) => listeners.delete(listener),
      },
    },
  } as unknown as Parameters<typeof createDesktopBridge>[0];
  const bridge = createDesktopBridge(host);
  const stop = bridge.start();
  expect(postMessage).toHaveBeenCalledWith(
    expect.objectContaining({ type: 'state', documentGeneration: 2 }),
  );
  expect(localRuntimeAvailable()).toBe(true);
  stop();
});
