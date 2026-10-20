/** Нативный EventSource читает SSE и восстанавливает соединение.
 * Адаптер ограничивает подписку текущим запуском локального сервиса. */
import { API_URL } from './request';
import { ApiClientError } from './errors';
import { EVENT_STREAM_FAILURE_MESSAGE } from './policy';
import { ownedLocalRequest } from './runtimeAvailability';

export interface EventSubscriptionOptions<T> {
  event: string;
  signal?: AbortSignal;
  onData: (value: T) => void;
  onError: (error: Error) => void;
  isTerminal: (value: T) => boolean;
}

export function subscribeEvents<T>(
  path: string,
  options: EventSubscriptionOptions<T>,
): () => void {
  const ownership = ownedLocalRequest(options.signal);
  let stream: EventSource;
  try {
    ownership.assertCurrent();
    stream = new EventSource(`${API_URL}${path}`);
  } catch (error) {
    ownership.dispose();
    throw error;
  }
  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    stream.close();
    stream.removeEventListener(options.event, accept);
    stream.onerror = null;
    ownership.signal.removeEventListener('abort', stop);
    ownership.dispose();
  };
  const accept = (event: Event) => {
    if (stopped) return;
    try {
      ownership.assertCurrent();
      if (!(event instanceof MessageEvent) || typeof event.data !== 'string')
        throw new ApiClientError(
          'INVALID_RESPONSE',
          EVENT_STREAM_FAILURE_MESSAGE,
        );
      const value: T = JSON.parse(event.data);
      options.onData(value);
      if (options.isTerminal(value)) stop();
    } catch (error) {
      stop();
      options.onError(
        error instanceof Error
          ? error
          : new ApiClientError(
              'INVALID_RESPONSE',
              EVENT_STREAM_FAILURE_MESSAGE,
            ),
      );
    }
  };
  ownership.signal.addEventListener('abort', stop, { once: true });
  stream.addEventListener(options.event, accept);
  stream.onerror = () => {
    if (stopped) return;
    if (stream.readyState === EventSource.CLOSED) stop();
    options.onError(
      new ApiClientError(
        'EVENT_STREAM_UNAVAILABLE',
        EVENT_STREAM_FAILURE_MESSAGE,
      ),
    );
  };
  return stop;
}
