import type { ProjectWriteOptions } from '../contracts/projects';
import {
  LOCAL_MAPPING_ACK_TIMEOUT_MS,
  MAPPING_ACK_TIMEOUT_MESSAGE,
} from './policy';

/** Ожидание ответа записи назначений; отмена запроса не отменяет запись на сервере. */
export async function awaitMappingWrite<T>(
  write: (options: RequestInit) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new Error(MAPPING_ACK_TIMEOUT_MESSAGE)),
    LOCAL_MAPPING_ACK_TIMEOUT_MS,
  );
  try {
    const result = await write({ signal: controller.signal });
    controller.signal.throwIfAborted();
    return result;
  } finally {
    clearTimeout(timer);
  }
}

/** Привязывает запись к версии её предпросмотра, сохраняя общий кеш версий. */
export function withProjectWriteOptions(
  init: RequestInit,
  options?: ProjectWriteOptions,
): RequestInit {
  if (!options) return init;
  const headers = new Headers(init.headers);
  headers.set('If-Match', `"${options.expectedStateVersion}"`);
  return { ...init, headers };
}
