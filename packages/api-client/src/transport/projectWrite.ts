import type { ProjectWriteOptions } from '../contracts/projects';

/** Pin a write to its captured basis without changing the shared version cache. */
export function withProjectWriteOptions(
  init: RequestInit,
  options?: ProjectWriteOptions,
): RequestInit {
  if (!options) return init;
  const headers = new Headers(init.headers);
  headers.set('If-Match', `"${options.expectedStateVersion}"`);
  return { ...init, headers };
}
