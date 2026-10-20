import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  api,
  type AutomaticPlacementPreview,
  type PatternPreview,
} from '@green/api-client';
import { previewWorkspaceAutomatic } from './workspacePreviews';
import { completeSearchDomainPreview } from './completeSearchDomainPreview';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function response(measured: number, pending = 100): PatternPreview {
  return {
    planning_horizon_year: 20,
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

function automaticResponse(
  processedObjects: number,
  found = 0,
): AutomaticPlacementPreview {
  return {
    planning_horizon_year: 3,
    requested: 8,
    found,
    shortfall: 8 - found,
    selected_kind: 'tree',
    selection_basis: 'Проверено',
    species_revision_ids: [],
    search_domains: [
      {
        ...response(0, processedObjects < 10602 ? 100 : 0).search_domains![0],
        method: 'hybrid',
        processed_objects: processedObjects,
        total_objects: 10602,
      },
    ],
  };
}

describe('automatic domain continuation', () => {
  it('connects automatic placement transport to the continuation instead of returning the first pass', async () => {
    const request = {
      base_plan_version: 7,
      zone_id: 'zone',
      near: 'area' as const,
      plant_kind: 'auto' as const,
      target_count: 8,
    };
    const next = vi
      .spyOn(api, 'previewAutomaticPlacement')
      .mockResolvedValueOnce(automaticResponse(2304))
      .mockResolvedValueOnce(automaticResponse(10602, 8));
    const publish = vi.fn();

    const result = await previewWorkspaceAutomatic(
      'project',
      request,
      new AbortController().signal,
      publish,
    );

    expect(next).toHaveBeenCalledTimes(2);
    expect(next).toHaveBeenNthCalledWith(
      1,
      'project',
      request,
      expect.any(AbortSignal),
    );
    expect(next).toHaveBeenNthCalledWith(
      2,
      'project',
      request,
      expect.any(AbortSignal),
    );
    expect(publish).toHaveBeenCalledOnce();
    expect(result.found).toBe(8);
  });

  it('continues an automatic proposal instead of publishing an unfinished zero as final', async () => {
    const next = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValueOnce(automaticResponse(2304))
      .mockResolvedValueOnce(automaticResponse(4608))
      .mockResolvedValueOnce(automaticResponse(10602, 8));
    const publish = vi.fn<(progress: AutomaticPlacementPreview) => void>();

    const result = await completeSearchDomainPreview(
      next,
      new AbortController().signal,
      publish,
    );

    expect(next).toHaveBeenCalledTimes(3);
    expect(publish).toHaveBeenCalledTimes(2);
    expect(result.found).toBe(8);
    expect(result.search_domains?.[0].stop_reason).toBe('resolution');
  });

  it('distinguishes unfinished tree and completed shrub snapshots sharing a zone and revision', async () => {
    const completed = automaticResponse(10602).search_domains![0];
    const first = automaticResponse(2304);
    first.search_domains!.push(completed);
    const second = automaticResponse(4608);
    second.search_domains!.push(completed);
    const next = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second)
      .mockResolvedValueOnce(automaticResponse(10602, 8));

    const result = await completeSearchDomainPreview(
      next,
      new AbortController().signal,
    );

    expect(next).toHaveBeenCalledTimes(3);
    expect(result.found).toBe(8);
  });

  it('still stops a stalled unfinished snapshot beside a completed alternative', async () => {
    const unfinished = automaticResponse(2304);
    unfinished.search_domains!.push(
      automaticResponse(10602).search_domains![0],
    );
    const next = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValue(unfinished);

    await expect(
      completeSearchDomainPreview(next, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
    expect(next).toHaveBeenCalledTimes(2);
  });

  it('does not expose candidates returned alongside an unfinished automatic search', async () => {
    const unfinished = automaticResponse(2304, 5);
    unfinished.change_set = {
      id: 'unfinished-proposal',
      digest: 'digest',
      base_plan_version: 7,
      source: 'recommendation',
      label: 'Посадки',
      can_apply: true,
      additions: [],
      expires_at: '2026-10-09T18:00:00Z',
    };
    const next = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValueOnce(unfinished)
      .mockResolvedValueOnce(automaticResponse(10602, 8));
    const publish = vi.fn<(progress: AutomaticPlacementPreview) => void>();

    await completeSearchDomainPreview(
      next,
      new AbortController().signal,
      publish,
    );

    expect(publish).toHaveBeenCalledWith(
      expect.objectContaining({ found: 0, change_set: undefined }),
    );
    expect(unfinished.found).toBe(5);
    expect(unfinished.change_set?.can_apply).toBe(true);
  });

  it('keeps a genuinely completed automatic zero as the final result', async () => {
    const completed = automaticResponse(10602);
    const next = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValue(completed);
    const publish = vi.fn<(progress: AutomaticPlacementPreview) => void>();

    expect(
      await completeSearchDomainPreview(
        next,
        new AbortController().signal,
        publish,
      ),
    ).toBe(completed);
    expect(next).toHaveBeenCalledOnce();
    expect(publish).not.toHaveBeenCalled();
  });

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
    const result = await completeSearchDomainPreview(
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
      completeSearchDomainPreview(
        async () => result,
        new AbortController().signal,
      ),
    ).rejects.toThrow('Проверка области прервана');
  });
  it('continues without a fixed pass limit, even if refinement has not reduced area yet', async () => {
    let pass = 0;
    const next = vi.fn(async () => response(++pass, pass < 130 ? 100 : 0));
    const progress = vi.fn();
    const result = await completeSearchDomainPreview(
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
    await completeSearchDomainPreview(next, new AbortController().signal);
    expect(next).toHaveBeenCalledTimes(2);
  });
  it('does not retry a completed area with no available ground', async () => {
    const next = vi.fn(async () => response(10, 0));
    expect(
      (await completeSearchDomainPreview(next, new AbortController().signal))
        .accepted_count,
    ).toBe(0);
    expect(next).toHaveBeenCalledOnce();
  });
  it('does not spin when the backend returns the same checkpoint', async () => {
    const next = vi.fn(async () => response(1));
    await expect(
      completeSearchDomainPreview(next, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
    expect(next).toHaveBeenCalledTimes(2);
  });
  it('stops on checkpoint regression or revision change', async () => {
    const next = vi
      .fn()
      .mockResolvedValueOnce(response(3))
      .mockResolvedValueOnce(response(1));
    await expect(
      completeSearchDomainPreview(next, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
  });
  it('never starts the next batch after cancellation', async () => {
    const controller = new AbortController();
    const next = vi.fn(async () => response(1));
    await expect(
      completeSearchDomainPreview(next, controller.signal, () =>
        controller.abort(),
      ),
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
      completeSearchDomainPreview(next, new AbortController().signal, publish),
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
      completeSearchDomainPreview(
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
      completeSearchDomainPreview(next, controller.signal, publish),
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

const hybridPhaseResponse = (
  processed: number,
  complete = false,
): PatternPreview => {
  const preview = response(0, complete ? 0 : 100);
  Object.assign(preview.search_domains![0], {
    method: 'hybrid',
    final_check: 'prepared_geometry',
    processed_objects: processed,
    total_objects: 10602,
  });
  return preview;
};

describe('natural search requested and base horizon queues', () => {
  it.each([1536, 8960])(
    'requests the third pass when a new base queue starts at %i or lower',
    async (currentBefore) => {
      const first = hybridPhaseResponse(currentBefore);
      const currentDone = hybridPhaseResponse(10602, true).search_domains![0];
      const basePending = hybridPhaseResponse(1536);
      basePending.search_domains!.unshift(currentDone);
      const completed = hybridPhaseResponse(10602, true);
      completed.accepted_count = 3;
      const next = vi
        .fn()
        .mockResolvedValueOnce(first)
        .mockResolvedValueOnce(basePending)
        .mockResolvedValueOnce(completed);
      const publish = vi.fn();

      const result = await completeSearchDomainPreview(
        next,
        new AbortController().signal,
        publish,
      );
      expect(next).toHaveBeenCalledTimes(3);
      expect(publish).toHaveBeenCalledTimes(2);
      expect(
        publish.mock.calls[1][0].search_domains.map(
          (domain: { processed_objects: number }) => domain.processed_objects,
        ),
      ).toEqual([10602, 1536]);
      expect(publish.mock.calls[1][0].accepted_count).toBe(0);
      expect(publish.mock.calls[1][0].change_set).toBeUndefined();
      expect(result.accepted_count).toBe(3);
    },
  );

  it.each([1024, 1536])(
    'rejects a base queue that regresses or stalls at %i beside the completed current queue',
    async (baseNext) => {
      const currentDone = hybridPhaseResponse(10602, true).search_domains![0];
      const first = hybridPhaseResponse(1536);
      first.search_domains!.unshift(currentDone);
      const second = hybridPhaseResponse(baseNext);
      second.search_domains!.unshift(currentDone);
      const next = vi
        .fn()
        .mockResolvedValueOnce(first)
        .mockResolvedValueOnce(second);

      await expect(
        completeSearchDomainPreview(next, new AbortController().signal),
      ).rejects.toThrow('Проверка области прервана');
      expect(next).toHaveBeenCalledTimes(2);
    },
  );

  it.each(['revision', 'method'] as const)(
    'rejects mutation of the existing %s even while a base queue is appended',
    async (field) => {
      const first = hybridPhaseResponse(8960);
      const changed = hybridPhaseResponse(10602, true).search_domains![0];
      if (field === 'revision') changed.revision = 'another-capture-or-rules';
      else {
        changed.method = 'native_cells';
        changed.measured_cells = 10602;
      }
      const second = hybridPhaseResponse(1536);
      second.search_domains!.unshift(changed);
      const next = vi
        .fn()
        .mockResolvedValueOnce(first)
        .mockResolvedValueOnce(second);

      await expect(
        completeSearchDomainPreview(next, new AbortController().signal),
      ).rejects.toThrow('Проверка области прервана');
      expect(next).toHaveBeenCalledTimes(2);
    },
  );
});

const keyedDomain = (key: string, count: number, complete = false) => ({
  ...hybridPhaseResponse(count, complete).search_domains![0],
  progress_key: key,
  total_objects: 20000,
});

const variantResponse = (
  ...domains: NonNullable<PatternPreview['search_domains']>
): PatternPreview => ({ ...response(0), search_domains: domains });

describe('stable domain keys across automatic variants', () => {
  it('continues the reported tree-base insertion before a pending shrub', async () => {
    const next = vi
      .fn()
      .mockResolvedValueOnce(
        variantResponse(
          keyedDomain('tree:0', 8960),
          keyedDomain('shrub:0', 1536),
        ),
      )
      .mockResolvedValueOnce(
        variantResponse(
          keyedDomain('tree:0', 11543, true),
          keyedDomain('tree:1', 512),
          keyedDomain('shrub:0', 3584),
        ),
      )
      .mockResolvedValueOnce(
        variantResponse(
          keyedDomain('tree:0', 11543, true),
          keyedDomain('shrub:0', 10602, true),
        ),
      );
    await completeSearchDomainPreview(next, new AbortController().signal);
    expect(next).toHaveBeenCalledTimes(3);
  });

  it.each([
    ['tree', 'tree'],
    ['tree', 'shrub'],
    ['shrub', 'tree'],
    ['shrub', 'shrub'],
  ] as const)(
    'keeps identities when %s starts base preparation first and %s finishes first',
    async (starts, finishes) => {
      const variants = ['tree', 'shrub'] as const;
      const first = variantResponse(
        ...variants.map((kind) => keyedDomain(`${kind}:0`, 256)),
      );
      const second = variantResponse(
        ...variants.flatMap((kind) =>
          kind === starts
            ? [
                keyedDomain(`${kind}:0`, 1024, true),
                keyedDomain(`${kind}:1`, 64),
              ]
            : [keyedDomain(`${kind}:0`, 512)],
        ),
      );
      const third = variantResponse(
        ...variants.flatMap((kind) => [
          keyedDomain(`${kind}:0`, 1024, true),
          keyedDomain(`${kind}:1`, kind === starts ? 128 : 32),
        ]),
      );
      const fourth = variantResponse(
        ...variants.flatMap((kind) => [
          keyedDomain(`${kind}:0`, 1024, true),
          ...(kind === finishes ? [] : [keyedDomain(`${kind}:1`, 256)]),
        ]),
      );
      const final = variantResponse(
        ...variants.map((kind) => keyedDomain(`${kind}:0`, 1024, true)),
      );
      const next = vi
        .fn()
        .mockResolvedValueOnce(first)
        .mockResolvedValueOnce(second)
        .mockResolvedValueOnce(third)
        .mockResolvedValueOnce(fourth)
        .mockResolvedValueOnce(final);
      expect(
        await completeSearchDomainPreview(next, new AbortController().signal),
      ).toBe(final);
      expect(next).toHaveBeenCalledTimes(5);
    },
  );

  it.each(['counter', 'revision', 'method'] as const)(
    'still refuses keyed %s mutation after another variant inserts a queue',
    async (field) => {
      const first = variantResponse(
        keyedDomain('tree:0', 768),
        keyedDomain('shrub:0', 768),
      );
      const shrub = keyedDomain('shrub:0', 896);
      if (field === 'counter') shrub.processed_objects = 512;
      else if (field === 'revision') shrub.revision = 'changed-cad-or-rules';
      else {
        shrub.method = 'native_cells';
        shrub.measured_cells = 896;
      }
      const second = variantResponse(
        keyedDomain('tree:0', 1024, true),
        keyedDomain('tree:1', 64),
        shrub,
      );
      const next = vi
        .fn()
        .mockResolvedValueOnce(first)
        .mockResolvedValueOnce(second);
      await expect(
        completeSearchDomainPreview(next, new AbortController().signal),
      ).rejects.toThrow('Проверка области прервана');
      expect(next).toHaveBeenCalledTimes(2);
    },
  );

  it('still refuses a genuinely stalled keyed pair of base queues', async () => {
    const snapshot = variantResponse(
      keyedDomain('tree:0', 1024, true),
      keyedDomain('tree:1', 64),
      keyedDomain('shrub:0', 1024, true),
      keyedDomain('shrub:1', 128),
    );
    const next = vi.fn().mockResolvedValue(snapshot);
    await expect(
      completeSearchDomainPreview(next, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
    expect(next).toHaveBeenCalledTimes(2);
  });

  it('retains the old index guard for null or missing progress keys', async () => {
    const first = variantResponse(
      keyedDomain('tree:0', 768),
      keyedDomain('shrub:0', 768),
    );
    first.search_domains!.forEach((domain) => {
      domain.progress_key = null;
    });
    const second = variantResponse(
      keyedDomain('tree:0', 1024, true),
      keyedDomain('tree:1', 64),
      keyedDomain('shrub:0', 896),
    );
    second.search_domains!.forEach((domain) => {
      delete domain.progress_key;
    });
    const next = vi
      .fn()
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);
    await expect(
      completeSearchDomainPreview(next, new AbortController().signal),
    ).rejects.toThrow('Проверка области прервана');
    expect(next).toHaveBeenCalledTimes(2);
  });
});
