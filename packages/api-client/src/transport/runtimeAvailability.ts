import { ApiClientError } from './errors';
import { RUNTIME_UNAVAILABLE_MESSAGE } from './policy';

let available = true;
let epoch = 0;
const requests = new Set<AbortController>();
const listeners = new Set<() => void>();

export function localRuntimeAvailable() {
  return available;
}
export function subscribeLocalRuntime(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Доступность подтверждает native-хост; отсутствие интернета не останавливает локальный API. */
export function setLocalRuntimeAvailable(next: boolean) {
  if (next === available) return;
  available = next;
  if (!next) {
    epoch += 1;
    for (const controller of requests) controller.abort();
  }
  for (const listener of listeners) listener();
}
const unavailable = (sent: boolean) =>
  new ApiClientError(
    'LOCAL_RUNTIME_UNAVAILABLE',
    RUNTIME_UNAVAILABLE_MESSAGE,
    {},
    { request_sent: sent },
  );
export const isUnsentLocalRequest = (error: unknown) =>
  error instanceof ApiClientError &&
  error.code === 'LOCAL_RUNTIME_UNAVAILABLE' &&
  error.details.request_sent === false;

export function ownedLocalRequest(signal?: AbortSignal | null) {
  if (!available) throw unavailable(false);
  const acceptedEpoch = epoch;
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  requests.add(controller);
  return {
    signal: controller.signal,
    assertCurrent() {
      if (!available || epoch !== acceptedEpoch) throw unavailable(true);
      controller.signal.throwIfAborted();
    },
    dispose() {
      requests.delete(controller);
      signal?.removeEventListener('abort', abort);
    },
  };
}
