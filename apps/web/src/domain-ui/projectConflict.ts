import { ApiClientError } from '@green/api-client';

export function isProjectConflict(error: unknown): error is ApiClientError {
  return error instanceof ApiClientError && error.code === 'PROJECT_VERSION_CONFLICT';
}
