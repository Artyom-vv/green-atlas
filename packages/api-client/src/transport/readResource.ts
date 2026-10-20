import { LOCAL_READ_TIMEOUT_MS, READ_FAILURE_MESSAGES } from './policy';

/** Чтение проекта и карты отменяет их владелец; длительность не определяет успешность. */
export async function readResource<T>(
  read: (options: RequestInit) => Promise<T>,
  resource: 'project' | 'map' | keyof typeof READ_FAILURE_MESSAGES,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  const timeout =
    resource === 'project' || resource === 'map'
      ? undefined
      : setTimeout(
          () => controller.abort(new Error(READ_FAILURE_MESSAGES[resource])),
          LOCAL_READ_TIMEOUT_MS,
        );
  try {
    const result = await read({
      cache: 'no-store',
      signal: controller.signal,
    });
    if (controller.signal.aborted) throw controller.signal.reason;
    return result;
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason;
    throw error;
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}
