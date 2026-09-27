import { MessageChannel as NodeMessageChannel } from 'node:worker_threads';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { scheduleGeometryWork } from './scheduleGeometryWork';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('geometry task scheduling', () => {
  it('posts cancellable user-visible work and guards an already dispatched task', () => {
    let dispatched: (() => void) | undefined;
    let signal: AbortSignal | undefined;
    const postTask = vi.fn(
      (callback: () => void, options: { signal: AbortSignal }) => {
        dispatched = callback;
        signal = options.signal;
        return Promise.resolve();
      },
    );
    vi.stubGlobal('scheduler', { postTask });
    const callback = vi.fn();
    const cancel = scheduleGeometryWork(callback);
    expect(postTask).toHaveBeenCalledWith(
      expect.any(Function),
      expect.objectContaining({ priority: 'user-visible' }),
    );
    expect(callback).not.toHaveBeenCalled();
    cancel();
    expect(signal?.aborted).toBe(true);
    dispatched?.();
    expect(callback).not.toHaveBeenCalled();
  });

  it('consumes the native abort rejection without reporting a task error', async () => {
    const postTask = vi.fn(
      (_: () => void, options: { signal: AbortSignal }) =>
        new Promise<void>((_, reject) => {
          options.signal.addEventListener('abort', () =>
            reject(options.signal.reason),
          );
        }),
    );
    vi.stubGlobal('scheduler', { postTask });
    const timer = vi.spyOn(globalThis, 'setTimeout');
    scheduleGeometryWork(vi.fn())();
    await Promise.resolve();
    expect(timer).not.toHaveBeenCalled();
  });

  it('does not suppress unexpected task failures', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('scheduler', {
      postTask: () => Promise.reject(new Error('Index failed')),
    });
    scheduleGeometryWork(vi.fn());
    await Promise.resolve();
    expect(() => vi.runAllTimers()).toThrow('Index failed');
  });

  it('uses MessageChannel when postTask is unavailable and closes cancelled work', async () => {
    vi.stubGlobal('scheduler', undefined);
    vi.stubGlobal('MessageChannel', NodeMessageChannel);
    const cancelled = vi.fn();
    scheduleGeometryWork(cancelled)();
    let ran = false;
    const completed = new Promise<void>((resolve) => {
      scheduleGeometryWork(() => {
        ran = true;
        resolve();
      });
    });
    expect(ran).toBe(false);
    await completed;
    expect(ran).toBe(true);
    expect(cancelled).not.toHaveBeenCalled();
  });

  it('cancels the timer fallback when neither scheduling API exists', () => {
    vi.useFakeTimers();
    vi.stubGlobal('scheduler', undefined);
    vi.stubGlobal('MessageChannel', undefined);
    const cancelled = vi.fn();
    const live = vi.fn();
    scheduleGeometryWork(cancelled)();
    scheduleGeometryWork(live);
    expect(live).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(cancelled).not.toHaveBeenCalled();
    expect(live).toHaveBeenCalledOnce();
  });
});
