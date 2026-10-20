import { ProjectVersions, requestScope } from './concurrency';
import { ownedLocalRequest } from './runtimeAvailability';
import { responseError } from './errors';

export const API_URL = import.meta.env?.VITE_API_URL ?? 'http://127.0.0.1:8000';

const projectVersions = new ProjectVersions();

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const ownership = ownedLocalRequest(init?.signal);
  try {
    const scope = requestScope(path, init?.method);
    const headers = projectVersions.headers(scope, init?.headers);
    const response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers,
      signal: ownership.signal,
    });
    ownership.assertCurrent();
    if (!response.ok) {
      const error = await responseError(response);
      ownership.assertCurrent();
      throw error;
    }
    if (response.status === 204) return undefined as T;
    const payload: T = await response.json();
    ownership.assertCurrent();
    projectVersions.observe(scope, response.headers, payload);
    return payload;
  } catch (error) {
    // Отмена не исключает поздний ответ; результат должен принадлежать текущему запуску.
    ownership.assertCurrent();
    throw error;
  } finally {
    ownership.dispose();
  }
}

export const json = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});
