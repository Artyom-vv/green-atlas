import { afterEach, expect, expectTypeOf, it, vi } from 'vitest';
import { projectsApi } from './projects';
import type { paths } from '../schema';
import { releaseApi } from './release';
import { setLocalRuntimeAvailable } from '../transport/runtimeAvailability';

afterEach(() => {
  setLocalRuntimeAvailable(true);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('starts physical capture preparation with one explicit POST', async () => {
  type WireOperation =
    paths['/api/projects/{project_id}/operations/cad-capture']['post']['responses'][202]['content']['application/json'];
  expectTypeOf<
    Awaited<ReturnType<typeof projectsApi.startCapturePreparation>>
  >().toEqualTypeOf<WireOperation>();
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        id: 'physical',
        kind: 'prepare_cad_capture',
        status: 'queued',
      }),
    ),
  );
  vi.stubGlobal('fetch', fetch);
  await projectsApi.startCapturePreparation('project');
  expect(fetch).toHaveBeenCalledOnce();
  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  expect(new URL(url).pathname).toBe(
    '/api/projects/project/operations/cad-capture',
  );
  expect(init.method).toBe('POST');
});

it.each([
  ['read', () => projectsApi.getProjectRead('slow-saved', false)],
  ['project', () => projectsApi.getProject('slow-saved', false)],
  ['map', () => projectsApi.getMapFeatures('slow-saved', [0, 0, 10, 10], 1)],
] as const)(
  'принимает длительное чтение %s без обрыва и повторного запроса',
  async (_name, read) => {
    vi.useFakeTimers();
    let finish!: (response: Response) => void;
    const fetch = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const pending = read().catch((error: unknown) => error);
    const [, options] = fetch.mock.calls[0] as [string, RequestInit];
    await vi.advanceTimersByTimeAsync(120_000);
    const abortedBeforeResponse = options.signal?.aborted;
    expect(fetch).toHaveBeenCalledOnce();
    const payload =
      _name === 'map'
        ? { type: 'FeatureCollection', features: [], total_count: 0 }
        : { id: 'slow-saved', state_version: 2 };
    finish(new Response(JSON.stringify(payload)));
    expect(abortedBeforeResponse).toBe(false);
    await expect(pending).resolves.toEqual(payload);
    expect(fetch.mock.calls[0][1].method ?? 'GET').toBe('GET');
    expect(vi.getTimerCount()).toBe(0);
  },
);

const ownedReads = [
  [
    'project',
    (signal?: AbortSignal) =>
      projectsApi.getProjectRead('owned-saved', false, signal),
  ],
  [
    'map',
    (signal?: AbortSignal) =>
      projectsApi.getMapFeatures('owned-saved', [0, 0, 10, 10], 1, signal),
  ],
] as const;

it.each(ownedReads)(
  'cancels a long %s read only when its observer leaves',
  async (_name, read) => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockImplementation(
      (_url, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener(
            'abort',
            () => reject(init.signal?.reason),
            { once: true },
          );
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    const pending = read(controller.signal).catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(120_000);
    const abortedBeforeLeaving = fetch.mock.calls[0][1].signal.aborted;
    const reason = new Error('Left workspace');
    controller.abort(reason);
    const failure = await pending;
    expect(abortedBeforeLeaving).toBe(false);
    expect(failure).toBe(reason);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][1].method ?? 'GET').toBe('GET');
    expect(vi.getTimerCount()).toBe(0);
  },
);

it.each(ownedReads)(
  'rejects the %s result if its observer cancelled before reading',
  async (_name, read) => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    const reason = new Error('Already left workspace');
    controller.abort(reason);
    await expect(read(controller.signal)).rejects.toBe(reason);
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
    expect(fetch).toHaveBeenCalledOnce();
  },
);

it.each(ownedReads)(
  'does not publish a late %s response after observer cancellation',
  async (_name, read) => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    const pending = read(controller.signal);
    const reason = new Error('Changed project');
    const failed = expect(pending).rejects.toBe(reason);
    controller.abort(reason);
    finish(new Response('{}'));
    await failed;
    expect(fetch).toHaveBeenCalledOnce();
  },
);

it.each(ownedReads)(
  'propagates the %s network failure without a write or automatic retry',
  async (_name, read) => {
    const error = new TypeError('Failed to fetch');
    const fetch = vi.fn().mockRejectedValue(error);
    vi.stubGlobal('fetch', fetch);
    await expect(read()).rejects.toBe(error);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][1].method ?? 'GET').toBe('GET');
  },
);

it.each(ownedReads)(
  'rejects the long %s read when the native host confirms Runtime shutdown',
  async (_name, read) => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockImplementation(
      (_url, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener(
            'abort',
            () => reject(init.signal?.reason),
            { once: true },
          );
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const pending = read().catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(120_000);
    const abortedBeforeShutdown = fetch.mock.calls[0][1].signal.aborted;
    setLocalRuntimeAvailable(false);
    const failure = await pending;
    expect(abortedBeforeShutdown).toBe(false);
    expect(failure).toMatchObject({ code: 'LOCAL_RUNTIME_UNAVAILABLE' });
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][1].method ?? 'GET').toBe('GET');
    expect(vi.getTimerCount()).toBe(0);
  },
);

it('turns a stalled catalog into a retryable read error without an empty result', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockImplementation(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener(
          'abort',
          () => reject(init.signal.reason),
          { once: true },
        );
      }),
  );
  vi.stubGlobal('fetch', fetch);
  const read = projectsApi.listProjects();
  const failed = expect(read).rejects.toThrow(
    'Не удалось загрузить список проектов за минуту',
  );
  await vi.advanceTimersByTimeAsync(60_000);
  await failed;
  expect(fetch).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it('rereads the project and local catalog without HTTP cache after a commit or reopen', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  await projectsApi.getProject('saved', false);
  await projectsApi.listProjects();
  expect(fetch.mock.calls.every(([, init]) => init.cache === 'no-store')).toBe(
    true,
  );
});

it('keeps the typed read port aligned with the generated GET route and legacy reads use one request', async () => {
  type WireRead =
    paths['/api/projects/{project_id}']['get']['responses'][200]['content']['application/json'];
  expectTypeOf<
    Awaited<ReturnType<typeof projectsApi.getProjectRead>>
  >().toEqualTypeOf<WireRead>();
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        id: 'saved',
        resume_kind: 'workspace',
        prepared_state: 'available',
      }),
    ),
  );
  vi.stubGlobal('fetch', fetch);
  const read = await projectsApi.getProject('saved', false);
  expect(fetch).toHaveBeenCalledOnce();
  expect(read).toMatchObject({
    resume_kind: 'workspace',
    prepared_state: 'available',
  });
});

it('сохраняет false и нулевые координаты в параметрах чтения', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);

  await projectsApi.getProjectRead('zero-values', false);
  await projectsApi.getMapFeatures('zero-values', [0, 0, 0, 0], 0);

  const projectAddress = new URL(fetch.mock.calls[0][0]);
  expect(projectAddress.searchParams.get('include_geometry')).toBe('false');
  const mapAddress = new URL(fetch.mock.calls[1][0]);
  expect(Object.fromEntries(mapAddress.searchParams)).toEqual({
    min_x: '0',
    min_y: '0',
    max_x: '0',
    max_y: '0',
    resolution: '0',
  });
});

it('передаёт специальные символы идентификатора и слоя без повторного кодирования', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  const projectId = 'проект /#%?';
  const layer = 'Сети / 50% & #?';

  await projectsApi.getSourceObjectReview(projectId, layer, 0);

  const [url] = fetch.mock.calls[0] as [string, RequestInit];
  const address = new URL(url);
  expect(address.pathname).toBe(
    `/api/projects/${encodeURIComponent(projectId)}/source-object-review`,
  );
  expect(address.searchParams.get('layer')).toBe(layer);
  expect(address.searchParams.get('offset')).toBe('0');
  expect(address.hash).toBe('');
});

it('возвращает undefined после удаления с ответом 204', async () => {
  const response = new Response(null, { status: 204 });
  const parseBody = vi.spyOn(response, 'json');
  const fetch = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fetch);

  await expect(
    projectsApi.deleteProject('deleted-project'),
  ).resolves.toBeUndefined();

  expect(fetch).toHaveBeenCalledOnce();
  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  expect(new URL(url).pathname).toBe('/api/projects/deleted-project');
  expect(init.method).toBe('DELETE');
  expect(init.body).toBeUndefined();
  expect(parseBody).not.toHaveBeenCalled();
});

it('never serves a cached operation status during recovery', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('null'));
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;

  await projectsApi.getLatestOperation(
    'project',
    'prepare_cad_project',
    signal,
  );

  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  expect(new URL(url).pathname).toBe('/api/projects/project/operations/latest');
  expect(init.cache).toBe('no-store');
  expect(init.signal?.aborted).toBe(false);
});

it.each([
  ['recognition', () => projectsApi.getLayerRecognition('saved')],
  ['recognition status', () => projectsApi.getLayerRecognitionStatus('saved')],
  ['operation', () => projectsApi.getOperation('saved', 'operation')],
  [
    'latest operation',
    () => projectsApi.getLatestOperation('saved', 'calculate_geometry'),
  ],
  ['release', () => releaseApi.getRelease('saved', 'release')],
])(
  'bounds the stalled %s read without sending a write or retry',
  async (_name, read) => {
    vi.useFakeTimers();
    const fetch = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener(
            'abort',
            () => reject(init.signal?.reason),
            {
              once: true,
            },
          );
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const failed = expect(read()).rejects.toThrow(/за минуту/);
    await vi.advanceTimersByTimeAsync(60_000);
    await failed;
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][1].method ?? 'GET').toBe('GET');
    expect(vi.getTimerCount()).toBe(0);
  },
);

it('uses the generated events route and rejects a malformed recognition snapshot before publishing', () => {
  let source: EventTarget & {
    close: ReturnType<typeof vi.fn>;
    readyState: number;
    onerror: unknown;
  };
  class TestEventSource extends EventTarget {
    static CLOSED = 2;
    readyState = 1;
    onerror = null;
    close = vi.fn();
    constructor(readonly url: string) {
      super();
      source = this;
      expect(new URL(url).pathname).toBe(
        '/api/projects/project/source-layer-review/events',
      );
    }
  }
  vi.stubGlobal('EventSource', TestEventSource);
  const onResult = vi.fn();
  const onError = vi.fn();
  projectsApi.subscribeLayerRecognition('project', { onResult, onError });
  source!.dispatchEvent(
    new MessageEvent('recognition', {
      data: JSON.stringify({ status: 'completed', proposals: [] }),
    }),
  );
  expect(onResult).not.toHaveBeenCalled();
  expect(onError).toHaveBeenCalledOnce();
  expect(source!.close).toHaveBeenCalledOnce();
});

/** Общий transport разбирает SSE; здесь проверяется только DTO квитанции и её маршрут. */
function operationStream() {
  let source: TestEventSource;
  class TestEventSource extends EventTarget {
    static CLOSED = 2;
    readyState = 1;
    onerror = null;
    close = vi.fn();
    constructor(readonly url: string) {
      super();
      source = this;
    }
  }
  vi.stubGlobal('EventSource', TestEventSource);
  const onResult = vi.fn();
  const onError = vi.fn();
  const stop = projectsApi.subscribeOperation('project /', 'operation /', {
    onResult,
    onError,
  });
  expect(new URL(source!.url).pathname).toBe(
    '/api/projects/project%20%2F/operations/operation%20%2F/events',
  );
  return {
    source: source!,
    stop,
    onResult,
    onError,
    send: (snapshot: unknown) =>
      source!.dispatchEvent(
        new MessageEvent('operation', { data: JSON.stringify(snapshot) }),
      ),
  };
}
const receipt = {
  id: 'operation /',
  project_id: 'project /',
  kind: 'calculate_geometry',
  status: 'running',
  project_state_version: 2,
  progress: 60,
  progress_mode: 'determinate',
  stage: 'Подготовка областей',
};
it.each(['completed', 'failed', 'cancelled', 'interrupted'])(
  'delivers the terminal operation %s once and closes the native stream',
  (status) => {
    const { source, send, onResult, onError } = operationStream();
    const snapshot = { ...receipt, status };
    send(snapshot);
    send(snapshot);
    expect(onResult).toHaveBeenCalledExactlyOnceWith(snapshot);
    expect(onError).not.toHaveBeenCalled();
    expect(source.close).toHaveBeenCalledOnce();
  },
);
it('keeps operation progress and cancelling on the same native connection', () => {
  const { source, send, onResult, onError, stop } = operationStream();
  send(receipt);
  send({ ...receipt, status: 'cancelling' });
  expect(onResult).toHaveBeenCalledTimes(2);
  expect(onError).not.toHaveBeenCalled();
  expect(source.close).not.toHaveBeenCalled();
  stop();
});
it.each([
  null,
  { ...receipt, status: 'unknown' },
  { ...receipt, kind: 'foreign' },
  { ...receipt, progress: 101 },
  { ...receipt, project_state_version: 0 },
  { ...receipt, processed_items: -1 },
])('rejects a malformed operation before publishing: %j', (snapshot) => {
  const { source, send, onResult, onError } = operationStream();
  send(snapshot);
  expect(onResult).not.toHaveBeenCalled();
  expect(onError).toHaveBeenCalledOnce();
  expect(source.close).toHaveBeenCalledOnce();
});

it('publishes a complete recognition event and closes its native connection on completion', () => {
  let source: EventTarget & { close: ReturnType<typeof vi.fn> };
  class TestEventSource extends EventTarget {
    static CLOSED = 2;
    readyState = 1;
    onerror = null;
    close = vi.fn();
    constructor() {
      super();
      source = this;
    }
  }
  vi.stubGlobal('EventSource', TestEventSource);
  const onResult = vi.fn();
  const onError = vi.fn();
  const snapshot = {
    source_sha256: 'drawing',
    provider: 'fixture',
    status: 'completed',
    categories: [],
    proposals: [],
    processed_count: 2,
    total_count: 2,
    stage_processed_count: 2,
    stage_total_count: 2,
    active_layer_ids: [],
  };
  projectsApi.subscribeLayerRecognition('project', { onResult, onError });
  source!.dispatchEvent(
    new MessageEvent('recognition', { data: JSON.stringify(snapshot) }),
  );
  expect(onResult).toHaveBeenCalledExactlyOnceWith(snapshot);
  expect(onError).not.toHaveBeenCalled();
  expect(source!.close).toHaveBeenCalledOnce();
});
