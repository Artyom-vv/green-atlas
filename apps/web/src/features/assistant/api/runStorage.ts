export const storageKey = (projectId: string) =>
  `green-atlas:agent-run:${projectId}`;

export function rememberRun(projectId: string, runId: string | null) {
  try {
    if (runId) window.sessionStorage.setItem(storageKey(projectId), runId);
    else window.sessionStorage.removeItem(storageKey(projectId));
  } catch {
    /* Optional continuity must not prevent working with the API. */
  }
}
