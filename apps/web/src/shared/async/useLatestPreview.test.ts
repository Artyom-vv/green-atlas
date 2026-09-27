import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useLatestPreview } from './useLatestPreview';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe('latest preview ownership', () => {
  it('publishes intermediate evidence without accepting it, and retains it on failure', async () => {
    const completion = deferred<string>();
    const accepted = vi.fn();
    let publish!: (value: string) => void;
    const { result } = renderHook(() =>
      useLatestPreview({
        execute: (_request: number, _signal, report) => {
          publish = report;
          return completion.promise;
        },
        onAccepted: accepted,
      }),
    );
    await act(async () => result.current.mutate(1));
    act(() => publish('32 percent'));
    expect(result.current.progress).toBe('32 percent');
    expect(result.current.isPending).toBe(true);
    expect(result.current.data).toBeUndefined();
    expect(accepted).not.toHaveBeenCalled();
    await act(async () => completion.reject(new Error('offline')));
    expect(result.current.progress).toBe('32 percent');
    expect(result.current.isPending).toBe(false);
    act(() => result.current.reset());
    act(() => publish('late'));
    expect(result.current.progress).toBeUndefined();
  });

  it('does not rerender consumers for repeated resets of an idle preview', () => {
    let renders = 0;
    const execute = vi.fn(async () => 'preview');
    const { result } = renderHook(() => {
      renders += 1;
      return useLatestPreview({ execute });
    });
    const initialRenders = renders;
    for (let frame = 0; frame < 60; frame += 1)
      act(() => result.current.reset());
    expect(renders).toBe(initialRenders);
    expect(execute).not.toHaveBeenCalled();
  });

  it('repeated reset aborts pending work and never accepts a late result', async () => {
    const response = deferred<string>();
    const accepted = vi.fn();
    let signal: AbortSignal | undefined;
    const { result } = renderHook(() =>
      useLatestPreview({
        execute: (_request: number, abort) => {
          signal = abort;
          return response.promise;
        },
        onAccepted: accepted,
      }),
    );
    await act(async () => result.current.mutate(1));
    act(() => result.current.reset());
    act(() => result.current.reset());
    expect(signal?.aborted).toBe(true);
    expect(result.current.isPending).toBe(false);
    await act(async () => response.resolve('cancelled'));
    expect(result.current.data).toBeUndefined();
    expect(accepted).not.toHaveBeenCalled();
  });

  it('reports a failed preview consumer and keeps the response unapplied', async () => {
    const failure = new Error('preview geometry could not be displayed');
    const { result } = renderHook(() =>
      useLatestPreview({
        execute: async () => 'preview',
        onAccepted: () => {
          throw failure;
        },
      }),
    );
    await act(async () => result.current.mutate(undefined));
    expect(result.current.error).toBe(failure);
    expect(result.current.data).toBeUndefined();
    expect(result.current.isPending).toBe(false);
  });

  it('retires a preview when its source version or form basis changes', async () => {
    const request = deferred<string>();
    const execute = vi.fn(() => request.promise);
    const { result, rerender } = renderHook(
      ({ basis }) => useLatestPreview({ scopeKey: basis, execute }),
      { initialProps: { basis: 'v1:row40' } },
    );
    act(() => result.current.mutate(undefined));
    await act(async () => request.resolve('valid-for-40'));
    expect(result.current.data).toBe('valid-for-40');
    rerender({ basis: 'v1:row80' });
    expect(result.current.data).toBeUndefined();
    rerender({ basis: 'v1:row40' });
    expect(result.current.data).toBeUndefined();
  });
  it('ignores an older success even if the transport ignores AbortSignal', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const accepted = vi.fn();
    const { result } = renderHook(() =>
      useLatestPreview({
        execute: (id: number) => (id === 1 ? first.promise : second.promise),
        onAccepted: accepted,
      }),
    );
    act(() => result.current.mutate(1));
    act(() => result.current.mutate(2));
    await act(async () => second.resolve('new'));
    await act(async () => first.resolve('old'));
    expect(result.current.data).toBe('new');
    expect(accepted).toHaveBeenCalledExactlyOnceWith('new', 2);
  });

  it('late errors cannot clear a newer pending request, and cancel retires it', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const accepted = vi.fn();
    const { result } = renderHook(() =>
      useLatestPreview({
        execute: (id: number) => (id === 1 ? first.promise : second.promise),
        onAccepted: accepted,
      }),
    );
    act(() => result.current.mutate(1));
    act(() => result.current.mutate(2));
    await act(async () => first.reject(new Error('late')));
    expect(result.current.isPending).toBe(true);
    expect(result.current.error).toBeUndefined();
    act(() => result.current.cancel());
    await act(async () => second.resolve('cancelled'));
    expect(result.current.data).toBeUndefined();
    expect(accepted).not.toHaveBeenCalled();
  });

  it('does not publish after its project is unmounted', async () => {
    const request = deferred<string>();
    const accepted = vi.fn();
    const { result, unmount } = renderHook(() =>
      useLatestPreview({
        execute: () => request.promise,
        onAccepted: accepted,
      }),
    );
    act(() => result.current.mutate(undefined));
    unmount();
    await act(async () => request.resolve('old-project'));
    expect(accepted).not.toHaveBeenCalled();
  });
});
