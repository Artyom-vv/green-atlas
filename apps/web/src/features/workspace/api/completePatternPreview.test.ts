import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import { completePatternPreview } from './completePatternPreview';

afterEach(() => vi.useRealTimers());

function response(measured: number, pending = 100): PatternPreview {
  return {
    pattern_id: 'pattern',
    type: 'fill',
    requested_count: 10,
    accepted_count: 0,
    generated_count: 0,
    rejected_count: 0,
    capacity_shortfall: 0,
    data_confidence: 'limited',
    skipped: [],
    search_domains: [
      {
        zone_id: 'zone',
        revision: 'same',
        method: 'native_cells',
        final_check: 'autocad',
        processed_objects: 0,
        total_objects: 0,
        cache_hits: 0,
        geometry: {},
        unresolved_geometry: {},
        available_area_m2: 0,
        excluded_area_m2: 0,
        unresolved_area_m2: 100 - pending,
        pending_area_m2: pending,
        minimum_cell_m: 0.5,
        measured_cells: measured,
        elapsed_s: measured,
        stop_reason: pending ? 'time_limit' : 'resolution',
      },
    ],
  };
}

describe('automatic domain continuation', () => {
  it('continues hybrid object batches even though the legacy cell counter stays zero', async () => {
    let pass = 0;
    const next = vi.fn(async () => {
      const result = response(0, ++pass < 7 ? 100 : 0);
      Object.assign(result.search_domains![0], {
        method: 'hybrid',
        processed_objects: pass * 256,
        total_objects: 1792,
      });
      return result;
    });
    const result = await completePatternPreview(
      next,
      new AbortController().signal,
    );
    expect(next).toHaveBeenCalledTimes(7);
    expect(result.search_domains![0].processed_objects).toBe(1792);
  });
  it('still rejects a stalled hybrid object queue', async () => {
    const result = response(0);
    Object.assign(result.search_domains![0], {
      method: 'hybrid',
      processed_objects: 256,
    });
    await expect(
      completePatternPreview(async () => result, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
  });
  it('continues without a fixed pass limit, even if refinement has not reduced area yet', async () => {
    let pass = 0;
    const next = vi.fn(async () => response(++pass, pass < 130 ? 100 : 0));
    const progress = vi.fn();
    const result = await completePatternPreview(
      next,
      new AbortController().signal,
      progress,
    );
    expect(next).toHaveBeenCalledTimes(130);
    expect(progress).toHaveBeenCalledTimes(129);
    expect(result.search_domains?.[0].unresolved_area_m2).toBe(100);
  });
  it('stops when all domains complete, not when the first one does', async () => {
    const first = response(1, 0);
    first.search_domains!.push({
      ...response(1).search_domains![0],
      zone_id: 'second',
    });
    const next = vi
      .fn()
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(response(2, 0));
    await completePatternPreview(next, new AbortController().signal);
    expect(next).toHaveBeenCalledTimes(2);
  });
  it('does not retry a completed area with no available ground', async () => {
    const next = vi.fn(async () => response(10, 0));
    expect(
      (await completePatternPreview(next, new AbortController().signal))
        .accepted_count,
    ).toBe(0);
    expect(next).toHaveBeenCalledOnce();
  });
  it('does not spin when the backend returns the same checkpoint', async () => {
    const next = vi.fn(async () => response(1));
    await expect(
      completePatternPreview(next, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
    expect(next).toHaveBeenCalledTimes(2);
  });
  it('stops on checkpoint regression or revision change', async () => {
    const next = vi
      .fn()
      .mockResolvedValueOnce(response(3))
      .mockResolvedValueOnce(response(1));
    await expect(
      completePatternPreview(next, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
  });
  it('never starts the next batch after cancellation', async () => {
    const controller = new AbortController();
    const next = vi.fn(async () => response(1));
    await expect(
      completePatternPreview(next, controller.signal, () => controller.abort()),
    ).rejects.toMatchObject({ name: 'AbortError' });
    expect(next).toHaveBeenCalledOnce();
  });
  it('does not endlessly retry a CAD or transport error', async () => {
    const error = new Error('AutoCAD не ответил на запрос');
    const next = vi
      .fn()
      .mockResolvedValueOnce(response(1))
      .mockRejectedValueOnce(error);
    const publish = vi.fn();
    await expect(
      completePatternPreview(next, new AbortController().signal, publish),
    ).rejects.toBe(error);
    expect(publish).toHaveBeenCalledOnce();
    expect(next).toHaveBeenCalledTimes(2);
  });
  it('stops a silent HTTP pass and aborts its transport without retrying', async () => {
    vi.useFakeTimers();
    let transportSignal: AbortSignal | undefined;
    const next = vi.fn(async (signal: AbortSignal) => {
      transportSignal = signal;
      return new Promise<PatternPreview>(() => {});
    });
    const failure = expect(
      completePatternPreview(
        next,
        new AbortController().signal,
        undefined,
        3000,
      ),
    ).rejects.toThrow('Ответ по текущей партии не получен');
    await vi.advanceTimersByTimeAsync(3000);
    await failure;
    expect(transportSignal?.aborted).toBe(true);
    expect(next).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
  it('cancels immediately even if a transport ignores abort and replies late', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    let reply: ((value: PatternPreview) => void) | undefined;
    const next = vi.fn(
      () =>
        new Promise<PatternPreview>((resolve) => {
          reply = resolve;
        }),
    );
    const publish = vi.fn();
    const failure = expect(
      completePatternPreview(next, controller.signal, publish),
    ).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await failure;
    reply!(response(1));
    await vi.advanceTimersByTimeAsync(0);
    expect(publish).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});
