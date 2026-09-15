import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { awaitMapGeometryFit } from '@/entities/editor/model/mapFocus';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());
function map() {
  let completed: ((value: boolean) => void) | undefined;
  const cancel = vi.fn();
  const state = {
    ready: false,
    revision: 2,
    fit: vi.fn((callback: (value: boolean) => void) => {
      completed = callback;
      return cancel;
    }),
  };
  return { state, cancel, finish: (value: boolean) => completed?.(value) };
}
describe('map camera completion contract', () => {
  it('waits for ingested geometry and actual fit completion, even without any plan objects', async () => {
    const m = map();
    const signal = new AbortController().signal;
    const resolved = vi.fn();
    const result = awaitMapGeometryFit(() => m.state, 2, signal).then(resolved);
    await vi.advanceTimersByTimeAsync(100);
    expect(m.state.fit).not.toHaveBeenCalled();
    m.state.ready = true;
    await vi.advanceTimersByTimeAsync(20);
    expect(m.state.fit).toHaveBeenCalledOnce();
    expect(resolved).not.toHaveBeenCalled();
    m.finish(true);
    await result;
    expect(resolved).toHaveBeenCalledWith({ status: 'completed' });
  });
  it('waits for the requested geometry version instead of fitting a previous viewport cache', async () => {
    const m = map();
    m.state.ready = true;
    m.state.revision = 1;
    const result = awaitMapGeometryFit(
      () => m.state,
      2,
      new AbortController().signal,
    );
    await vi.advanceTimersByTimeAsync(50);
    expect(m.state.fit).not.toHaveBeenCalled();
    m.state.revision = 2;
    await vi.advanceTimersByTimeAsync(20);
    m.finish(true);
    expect(await result).toEqual({ status: 'completed' });
  });
  it('does not claim completion when OpenLayers reports an interrupted animation', async () => {
    const m = map();
    m.state.ready = true;
    const result = awaitMapGeometryFit(
      () => m.state,
      2,
      new AbortController().signal,
    );
    await vi.advanceTimersByTimeAsync(20);
    m.finish(false);
    expect(await result).toEqual({
      status: 'failed',
      error_code: 'FOCUS_INTERRUPTED',
    });
  });
  it('aborts its active camera operation and ignores a late completion callback', async () => {
    const m = map();
    m.state.ready = true;
    const controller = new AbortController();
    const result = awaitMapGeometryFit(() => m.state, 2, controller.signal);
    await vi.advanceTimersByTimeAsync(20);
    controller.abort();
    m.finish(true);
    expect(await result).toEqual({
      status: 'cancelled',
      error_code: 'FOCUS_INTERRUPTED',
    });
    expect(m.cancel).toHaveBeenCalledOnce();
  });
  it('rejects a geometry change during fitting and cancels the old animation', async () => {
    const m = map();
    m.state.ready = true;
    const result = awaitMapGeometryFit(
      () => m.state,
      2,
      new AbortController().signal,
    );
    await vi.advanceTimersByTimeAsync(20);
    m.state.revision = 3;
    await vi.advanceTimersByTimeAsync(20);
    expect(await result).toEqual({
      status: 'failed',
      error_code: 'CONTROL_STALE',
    });
    expect(m.cancel).toHaveBeenCalledOnce();
  });
  it('returns a bounded not-ready failure without moving an unavailable map', async () => {
    const m = map();
    const result = awaitMapGeometryFit(
      () => m.state,
      2,
      new AbortController().signal,
      100,
    );
    await vi.advanceTimersByTimeAsync(110);
    expect(await result).toEqual({
      status: 'failed',
      error_code: 'MAP_NOT_READY',
    });
    expect(m.state.fit).not.toHaveBeenCalled();
  });
});
