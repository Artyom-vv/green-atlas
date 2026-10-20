import { setLocalRuntimeAvailable } from '@green/api-client';
import {
  DESKTOP_PROTOCOL_VERSION,
  parseDesktopMessage,
  type DesktopNavigationRequest,
  type DesktopWorkState,
} from './protocol';

interface WebView2Channel {
  postMessage: (value: unknown) => void;
  addEventListener: (
    type: 'message',
    listener: (event: { data: unknown }) => void,
  ) => void;
  removeEventListener: (
    type: 'message',
    listener: (event: { data: unknown }) => void,
  ) => void;
}

interface NativeWindow extends Window {
  webkit?: {
    messageHandlers?: {
      greenAtlasDesktop?: { postMessage: (value: unknown) => void };
    };
  };
  chrome?: { webview?: WebView2Channel };
  __greenAtlasDesktopReceive?: (value: unknown) => void;
}

type NavigationHandler = (request: DesktopNavigationRequest) => void;
const idleState: DesktopWorkState = {
  hasDraft: false,
  writeOutcome: 'idle',
  readRecovery: false,
  editingBlocked: false,
};

/** Канал документа передаёт намерения хоста; состоянием работы владеют features. */
export function createDesktopBridge(host: NativeWindow) {
  const native = Boolean(
    host.webkit?.messageHandlers?.greenAtlasDesktop || host.chrome?.webview,
  );
  if (native) setLocalRuntimeAvailable(false);
  let generation: number | undefined;
  let lastRequestId = 0;
  let cancelledRequestId = 0;
  let handler: NavigationHandler | undefined;
  let state = idleState;
  let pickerId = 0;
  const pickers = new Map<
    number,
    { field: string; complete: (value: string | undefined) => void }
  >();
  const clearPickers = () => {
    for (const item of pickers.values()) item.complete(undefined);
    pickers.clear();
  };
  const post = (value: unknown) => {
    const mac = host.webkit?.messageHandlers?.greenAtlasDesktop;
    if (mac) mac.postMessage(value);
    else host.chrome?.webview?.postMessage(value);
  };
  const decision = (request: DesktopNavigationRequest, allow: boolean) => {
    if (
      request.documentGeneration !== generation ||
      request.requestId !== lastRequestId ||
      request.requestId <= cancelledRequestId
    )
      return;
    post({
      schemaVersion: DESKTOP_PROTOCOL_VERSION,
      type: 'decision',
      origin: host.location.origin,
      documentGeneration: generation,
      requestId: request.requestId,
      allow,
    });
  };
  const publish = () => {
    if (generation === undefined) return;
    post({
      schemaVersion: DESKTOP_PROTOCOL_VERSION,
      type: 'state',
      origin: host.location.origin,
      documentGeneration: generation,
      ...state,
    });
  };
  return {
    start() {
      if (native) setLocalRuntimeAvailable(false);
      let active = true;
      const receive = (input: unknown) => {
        if (!active) return;
        if (input && typeof input === 'object') {
          const picked = input as Record<string, unknown>;
          if (
            picked.type === 'dependencyChosen' &&
            picked.schemaVersion === 1 &&
            picked.origin === host.location.origin &&
            picked.documentGeneration === generation &&
            typeof picked.requestId === 'number'
          ) {
            const pending = pickers.get(picked.requestId);
            if (
              pending &&
              picked.field === pending.field &&
              (picked.path === null ||
                (typeof picked.path === 'string' && picked.path.length <= 8192))
            ) {
              pending.complete(
                typeof picked.path === 'string' ? picked.path : undefined,
              );
              pickers.delete(picked.requestId);
            }
            return;
          }
        }
        const value = parseDesktopMessage(input, host.location.origin);
        if (!value) return;
        if (value.type === 'capabilities') {
          // Поздний ответ хоста не восстанавливает прежнее поколение документа.
          if (
            generation !== undefined &&
            value.documentGeneration <= generation
          )
            return;
          generation = value.documentGeneration;
          clearPickers();
          lastRequestId = 0;
          cancelledRequestId = 0;
          publish();
          return;
        }
        if (value.documentGeneration !== generation) return;
        if (
          value.type === 'runtimeUnavailable' ||
          value.type === 'runtimeRestored'
        ) {
          if (value.type === 'runtimeUnavailable') {
            cancelledRequestId = lastRequestId;
            clearPickers();
          }
          setLocalRuntimeAvailable(value.type === 'runtimeRestored');
          return;
        }
        if (value.type !== 'navigationRequest') return;
        if (
          value.documentGeneration !== generation ||
          value.requestId <= lastRequestId
        )
          return;
        lastRequestId = value.requestId;
        if (handler) handler(value);
        else
          decision(
            value,
            !/^\/(?:projects\/[^/]+\/(?:workspace|setup)(?:\/|$)|connect\/autocad\/)/.test(
              host.location.pathname,
            ),
          );
      };
      host.__greenAtlasDesktopReceive = receive;
      // WKWebView сохраняет свой callback. WebView2 доставляет JSON через
      // собственный канал, без выполнения строки JavaScript в документе.
      const webview = host.webkit?.messageHandlers?.greenAtlasDesktop
        ? undefined
        : host.chrome?.webview;
      const receiveWebView2 = (event: { data: unknown }) => receive(event.data);
      webview?.addEventListener('message', receiveWebView2);
      post({
        schemaVersion: DESKTOP_PROTOCOL_VERSION,
        type: 'ready',
        origin: host.location.origin,
      });
      return () => {
        active = false;
        webview?.removeEventListener('message', receiveWebView2);
        if (host.__greenAtlasDesktopReceive === receive)
          delete host.__greenAtlasDesktopReceive;
        generation = undefined;
        clearPickers();
        lastRequestId = 0;
        cancelledRequestId = 0;
      };
    },
    register(next: NavigationHandler) {
      handler = next;
      return () => {
        if (handler === next) {
          handler = undefined;
        }
      };
    },
    updateState(next: DesktopWorkState) {
      state = next;
      publish();
    },
    decision,
    canChooseDependency: Boolean(host.chrome?.webview),
    chooseDependency(field: string): Promise<string | undefined> {
      if (
        !host.chrome?.webview ||
        generation === undefined ||
        ![
          'autocad_root',
          'autocad_template',
          'blender',
          'botaniq_root',
          'pbr_root',
        ].includes(field)
      )
        return Promise.resolve(undefined);
      const requestId = ++pickerId;
      return new Promise((resolve) => {
        const timer = host.setTimeout(() => {
          pickers.delete(requestId);
          resolve(undefined);
        }, 120_000);
        pickers.set(requestId, {
          field,
          complete: (value) => {
            host.clearTimeout(timer);
            resolve(value);
          },
        });
        post({
          schemaVersion: 1,
          type: 'chooseDependency',
          origin: host.location.origin,
          documentGeneration: generation,
          requestId,
          field,
        });
      });
    },
  };
}

export const desktopBridge = createDesktopBridge(window);
