import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as previewApi from '../api/previewPlanChanges';
import {
  useMoveValidation,
  type MoveValidationOptions,
} from './useMoveValidation';

type Preview = Awaited<ReturnType<typeof previewApi.previewPlanChanges>>;
const accepted: Preview = {
  id: 'preview-1',
  digest: 'digest',
  base_plan_version: 1,
  source: 'group',
  label: 'Перемещение посадки',
  can_apply: true,
  expires_at: '2026-09-14T23:00:00Z',
};
const initial: MoveValidationOptions = {
  projectId: 'a',
  planVersion: 1,
  stateVersion: 3,
  geometryVersion: 2,
  active: true,
  objects: [
    {
      id: 'tree-1',
      kind: 'tree',
      x: 0,
      y: 0,
      radius: 2,
      size_class: 'standard',
      spacing_policy: 'balanced',
      locked: false,
      status: 'valid',
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('move preview lifecycle', () => {
  it('does not rerender the editor for idle pointer coordinates outside move', () => {
    let renders = 0;
    const execute = vi.spyOn(previewApi, 'previewPlanChanges');
    const { result } = renderHook(() => {
      renders += 1;
      return useMoveValidation({ ...initial, objects: [] });
    });
    const initialRenders = renders;
    for (let frame = 0; frame < 60; frame += 1)
      act(() => result.current.check(undefined));
    expect(renders).toBe(initialRenders);
    expect(execute).not.toHaveBeenCalled();
    expect(result.current.validation).toBeUndefined();
  });

  it('cancels the debounce on clear and unmount before requesting a preview', async () => {
    const preview = vi
      .spyOn(previewApi, 'previewPlanChanges')
      .mockResolvedValue(accepted);
    const { result, unmount } = renderHook(() => useMoveValidation(initial));
    act(() => result.current.check([10, 10]));
    expect(result.current.validation?.status).toBe('checking');
    act(() => result.current.clear());
    expect(result.current.validation).toBeUndefined();
    await settle();
    expect(preview).not.toHaveBeenCalled();
    act(() => result.current.check([20, 20]));
    unmount();
    await settle();
    expect(preview).not.toHaveBeenCalled();
  });

  it('does not resurrect a cancelled scheduled check when scope changes A to B to A', async () => {
    const preview = vi
      .spyOn(previewApi, 'previewPlanChanges')
      .mockResolvedValue(accepted);
    const { result, rerender } = renderHook(useMoveValidation, {
      initialProps: initial,
    });
    act(() => result.current.check([10, 10]));
    expect(result.current.validation?.status).toBe('checking');
    rerender({ ...initial, projectId: 'b' });
    expect(result.current.validation).toBeUndefined();
    rerender(initial);
    expect(result.current.validation).toBeUndefined();
    await settle();
    expect(preview).not.toHaveBeenCalled();
    act(() => result.current.check([20, 20]));
    await settle();
    expect(preview).toHaveBeenCalledTimes(1);
    expect(result.current.validation?.status).toBe('allowed');
  });

  it('aborts the previous scope and ignores its late error while a new check is pending', async () => {
    const old = deferred<Preview>();
    const current = deferred<Preview>();
    const preview = vi
      .spyOn(previewApi, 'previewPlanChanges')
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(current.promise);
    const { result, rerender } = renderHook(useMoveValidation, {
      initialProps: initial,
    });
    act(() => result.current.check([10, 10]));
    await settle();
    const oldSignal = preview.mock.calls[0][2];
    rerender({ ...initial, stateVersion: 4 });
    expect(oldSignal.aborted).toBe(true);
    act(() => result.current.check([30, 30]));
    await settle();
    await act(async () => old.reject(new Error('late failure')));
    expect(result.current.validation?.status).toBe('checking');
    expect(preview.mock.calls[1][2].aborted).toBe(false);
    await act(async () => current.resolve(accepted));
    expect(result.current.validation?.status).toBe('allowed');
    expect(result.current.validation?.reason).toBe('Можно переместить');
  });
});
