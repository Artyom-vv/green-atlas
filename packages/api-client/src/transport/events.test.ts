import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { subscribeEvents } from './events';
import { setLocalRuntimeAvailable } from './runtimeAvailability';

class ControlledEventSource extends EventTarget {
  static readonly CLOSED = 2;
  readyState = 1;
  onerror: ((event: Event) => void) | null = null;
  private listener?: EventListener;
  close = vi.fn(() => {
    this.readyState = ControlledEventSource.CLOSED;
  });

  constructor(readonly url: string) {
    super();
    sources.push(this);
  }

  override addEventListener(
    type: string,
    callback: EventListenerOrEventListenerObject | null,
    options?: boolean | AddEventListenerOptions,
  ) {
    super.addEventListener(type, callback, options);
    if (typeof callback === 'function') this.listener = callback;
  }

  deliver(data: string) {
    this.dispatchEvent(new MessageEvent('recognition', { data }));
  }

  deliverLate(data: string) {
    this.listener?.(new MessageEvent('recognition', { data }));
  }
}

interface Snapshot {
  status: 'running' | 'completed' | 'failed';
  processed: number;
}

const sources: ControlledEventSource[] = [];
const stops: (() => void)[] = [];
const running = JSON.stringify({ status: 'running', processed: 24 });

function observe(signal?: AbortSignal) {
  const onData = vi.fn();
  const onError = vi.fn();
  stops.push(
    subscribeEvents<Snapshot | null>('/api/events', {
      event: 'recognition',
      signal,
      onData,
      onError,
      isTerminal: (snapshot) =>
        snapshot === null || snapshot.status !== 'running',
    }),
  );
  return { onData, onError, source: sources.at(-1)! };
}

beforeEach(() => {
  vi.stubGlobal('EventSource', ControlledEventSource);
  setLocalRuntimeAvailable(true);
});
afterEach(() => {
  stops.splice(0).forEach((stop) => stop());
  sources.splice(0);
  setLocalRuntimeAvailable(true);
  vi.unstubAllGlobals();
});

it('delivers a native SSE message through one persistent source', () => {
  const state = observe();
  state.source.deliver(running);
  expect(state.onData).toHaveBeenCalledExactlyOnceWith({
    status: 'running',
    processed: 24,
  });
  expect(state.source.url).toMatch(/\/api\/events$/);
  expect(sources).toHaveLength(1);
  expect(state.source.close).not.toHaveBeenCalled();
});

it('leaves reconnection to EventSource and accepts its fresh snapshot without another command', () => {
  const state = observe();
  state.source.readyState = 0;
  state.source.onerror?.(new Event('error'));
  expect(state.onError).toHaveBeenCalledOnce();
  expect(state.source.close).not.toHaveBeenCalled();
  state.source.readyState = 1;
  state.source.deliver(running);
  expect(state.onData).toHaveBeenCalledOnce();
  expect(sources).toHaveLength(1);
});

it.each(['completed', 'failed', null] as const)(
  'closes after terminal snapshot %s before the browser can reconnect',
  (status) => {
    const state = observe();
    const snapshot = status === null ? null : { status, processed: 24 };
    state.source.deliver(JSON.stringify(snapshot));
    expect(state.onData).toHaveBeenCalledExactlyOnceWith(snapshot);
    expect(state.source.close).toHaveBeenCalledOnce();
    state.source.deliverLate(running);
    expect(state.onData).toHaveBeenCalledOnce();
  },
);

it('ignores a queued late message after the owner aborts', () => {
  const controller = new AbortController();
  const state = observe(controller.signal);
  controller.abort();
  state.source.deliverLate(running);
  expect(state.source.close).toHaveBeenCalledOnce();
  expect(state.onData).not.toHaveBeenCalled();
  expect(state.onError).not.toHaveBeenCalled();
});

it('closes on runtime shutdown and never publishes into the next runtime epoch', () => {
  const old = observe();
  setLocalRuntimeAvailable(false);
  setLocalRuntimeAvailable(true);
  const fresh = observe();
  old.source.deliverLate(running);
  fresh.source.deliver(running);
  expect(old.source.close).toHaveBeenCalledOnce();
  expect(old.onData).not.toHaveBeenCalled();
  expect(fresh.onData).toHaveBeenCalledOnce();
});

it('does not connect an already aborted owner', () => {
  const controller = new AbortController();
  controller.abort();
  expect(() => observe(controller.signal)).toThrow();
  expect(sources).toHaveLength(0);
});

it('stops malformed JSON without continuing to publish later messages', () => {
  const state = observe();
  state.source.deliver('{');
  state.source.deliverLate(running);
  expect(state.onError).toHaveBeenCalledOnce();
  expect(state.onData).not.toHaveBeenCalled();
  expect(state.source.close).toHaveBeenCalledOnce();
});
