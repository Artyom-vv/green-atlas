import { afterEach, expect, it, vi } from 'vitest';
import { projectsApi } from '../api/projects';
import { request } from './request';
import {
  isUnsentLocalRequest,
  setLocalRuntimeAvailable,
} from './runtimeAvailability';

afterEach(() => {
  setLocalRuntimeAvailable(true);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('останавливает ожидание назначений без повторной записи и не считает её неотправленной', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockImplementation(
    (_url, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener(
          'abort',
          () => reject(init.signal?.reason),
          { once: true },
        );
      }),
  );
  vi.stubGlobal('fetch', fetch);
  const result = projectsApi
    .saveMappings('mapping-deadline', [])
    .catch((error: unknown) => error);
  const [, pending] = fetch.mock.calls[0] as [string, RequestInit];
  await vi.advanceTimersByTimeAsync(30_000);
  expect(pending.signal?.aborted).toBe(true);
  expect(isUnsentLocalRequest(await result)).toBe(false);
  await vi.advanceTimersByTimeAsync(120_000);
  expect(fetch).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it('не принимает позднее тело назначений и сохраняет ревизию следующей записи', async () => {
  const projectId = 'mapping-late-body';
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ id: projectId, state_version: 9 })),
    );
  vi.stubGlobal('fetch', fetch);
  await projectsApi.getProjectRead(projectId, false);
  vi.useFakeTimers();
  let finishBody!: (body: unknown) => void;
  const response = new Response();
  const bodyRead = vi.spyOn(response, 'json').mockImplementation(
    () =>
      new Promise((resolve) => {
        finishBody = resolve;
      }),
  );
  fetch.mockResolvedValue(response);
  const result = projectsApi
    .saveMappings(projectId, [])
    .catch((error: unknown) => error);
  await vi.advanceTimersByTimeAsync(0);
  expect(bodyRead).toHaveBeenCalledOnce();
  const [, pending] = fetch.mock.calls[1] as [string, RequestInit];
  await vi.advanceTimersByTimeAsync(30_000);
  expect(pending.signal?.aborted).toBe(true);
  finishBody({ id: projectId, state_version: 999 });
  expect(await result).toBe(pending.signal?.reason);
  fetch.mockResolvedValue(new Response('{}'));
  await projectsApi.saveMappings(projectId, []);
  const [, write] = fetch.mock.calls.at(-1)! as [string, RequestInit];
  expect(new Headers(write.headers).get('If-Match')).toBe('"9"');
  expect(vi.getTimerCount()).toBe(0);
});

it('освобождает таймер ответа назначений и не обрывает долгую отдельную операцию', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValue(new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  await projectsApi.saveMappings('mapping-finished', []);
  expect(vi.getTimerCount()).toBe(0);
  let finish!: (response: Response) => void;
  fetch.mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const operation = request(
    '/api/projects/mapping-finished/operations/geometry',
    { method: 'POST' },
  );
  const [, pending] = fetch.mock.calls.at(-1)! as [string, RequestInit];
  await vi.advanceTimersByTimeAsync(120_000);
  expect(pending.signal?.aborted).toBe(false);
  finish(new Response('{}'));
  await operation;
});

it('does not send any request while ownership of the runtime is unavailable', async () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  setLocalRuntimeAvailable(false);
  const failure = await request('/api/projects', { method: 'POST' }).catch(
    (error: unknown) => error,
  );
  expect(isUnsentLocalRequest(failure)).toBe(true);
  expect(fetch).not.toHaveBeenCalled();
});

it('aborts a sent request and rejects its late response even after restoration', async () => {
  let resolve!: (response: Response) => void;
  const fetch = vi.fn().mockImplementation(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal('fetch', fetch);
  const result = request('/api/projects/p', { method: 'POST' }).catch(
    (error: unknown) => error,
  );
  const [, init] = fetch.mock.calls[0] as [string, RequestInit];
  setLocalRuntimeAvailable(false);
  expect(init.signal?.aborted).toBe(true);
  setLocalRuntimeAvailable(true);
  resolve(new Response(JSON.stringify({ id: 'p', state_version: 999 })));
  const failure = await result;
  expect(failure).toMatchObject({
    code: 'LOCAL_RUNTIME_UNAVAILABLE',
    details: { request_sent: true },
  });
  expect(isUnsentLocalRequest(failure)).toBe(false);
  fetch.mockResolvedValue(
    new Response(JSON.stringify({ id: 'p', state_version: 2 })),
  );
  await expect(request('/api/projects/p')).resolves.toMatchObject({
    state_version: 2,
  });
  fetch.mockResolvedValue(
    new Response(JSON.stringify({ id: 'p', state_version: 3 })),
  );
  await request('/api/projects/p', { method: 'POST' });
  const [, freshWrite] = fetch.mock.calls.at(-1)! as [string, RequestInit];
  expect(new Headers(freshWrite.headers).get('If-Match')).toBe('"2"');
});

it('propagates caller cancellation into the owned request', async () => {
  const fetch = vi.fn().mockImplementation(
    (_url, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener(
          'abort',
          () => reject(new DOMException('aborted', 'AbortError')),
          { once: true },
        );
      }),
  );
  vi.stubGlobal('fetch', fetch);
  const caller = new AbortController();
  const result = request('/api/projects', { signal: caller.signal });
  caller.abort();
  await expect(result).rejects.toMatchObject({ name: 'AbortError' });
});

it('rejects a late cancelled body before it can replace the project revision', async () => {
  const projectPath = '/api/projects/cancelled-read';
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ id: 'cancelled-read', state_version: 9 })),
    );
  vi.stubGlobal('fetch', fetch);
  await request(projectPath);
  let finishBody!: (body: unknown) => void;
  const response = new Response();
  const bodyRead = vi.spyOn(response, 'json').mockImplementation(
    () =>
      new Promise((resolve) => {
        finishBody = resolve;
      }),
  );
  fetch.mockResolvedValue(response);
  const caller = new AbortController();
  const result = request(projectPath, { signal: caller.signal }).catch(
    (error: unknown) => error,
  );
  await vi.waitFor(() => expect(bodyRead).toHaveBeenCalledOnce());
  caller.abort(new Error('Read superseded'));
  finishBody({ id: 'cancelled-read', state_version: 7 });
  expect(await result).toBe(caller.signal.reason);
  fetch.mockResolvedValue(new Response('{}'));
  await request(projectPath, { method: 'POST' });
  const [, write] = fetch.mock.calls.at(-1)! as [string, RequestInit];
  expect(new Headers(write.headers).get('If-Match')).toBe('"9"');
});

it('отклоняет позднее чтение через projectsApi после перезапуска и сохраняет ревизию записи', async () => {
  const projectId = 'restart-through-projects';
  let finishRead!: (response: Response) => void;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ id: projectId, state_version: 9 })),
    )
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finishRead = resolve;
        }),
    )
    .mockResolvedValueOnce(new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  await projectsApi.getProjectRead(projectId, false);
  const read = projectsApi
    .getProjectRead(projectId, false)
    .catch((error: unknown) => error);
  const [, pendingRead] = fetch.mock.calls[1] as [string, RequestInit];

  setLocalRuntimeAvailable(false);
  expect(pendingRead.signal?.aborted).toBe(true);
  setLocalRuntimeAvailable(true);
  finishRead(
    new Response(JSON.stringify({ id: projectId, state_version: 999 })),
  );

  expect(await read).toMatchObject({
    code: 'LOCAL_RUNTIME_UNAVAILABLE',
    details: { request_sent: true },
  });
  await projectsApi.saveMappings(projectId, []);
  const [, write] = fetch.mock.calls[2] as [string, RequestInit];
  expect(new Headers(write.headers).get('If-Match')).toBe('"9"');
});
