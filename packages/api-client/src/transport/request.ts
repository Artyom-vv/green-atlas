import { ProjectVersions, requestScope } from './concurrency';
import { responseError } from './errors';

export const API_URL = import.meta.env?.VITE_API_URL ?? 'http://127.0.0.1:8000';

const projectVersions = new ProjectVersions();

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const scope = requestScope(path, init?.method);
  const headers = projectVersions.headers(scope, init?.headers);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });

  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return undefined as T;

  const payload: T = await response.json();
  projectVersions.observe(scope, response.headers, payload);
  return payload;
}

export const json = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});
