import {
  parseReleaseDraftDocument,
  type ReleaseDraftContext,
  type ReleaseDraftDocument,
} from './releaseDraft';

export type ReleaseDraftSnapshot = ReleaseDraftDocument & {
  releaseId?: string;
  error: Error | null;
  storageAvailable: boolean;
  notice?: string;
  observedContext?: ReleaseDraftContext;
  recoveryReleaseId?: string;
  recoveryError?: Error;
};
const snapshots = new Map<string, ReleaseDraftSnapshot>();
const listeners = new Map<string, Set<() => void>>();
export const releaseDraftKey = (projectId: string) =>
  `green-atlas:release-draft:v1:${projectId}`;
const releaseKey = (projectId: string) => `green-atlas:release:${projectId}`;

export function getReleaseDraftSnapshot(
  projectId: string,
): ReleaseDraftSnapshot {
  const existing = snapshots.get(projectId);
  if (existing) return existing;
  let draft: ReleaseDraftDocument | undefined,
    releaseId: string | undefined,
    storageAvailable = true;
  try {
    draft = parseReleaseDraftDocument(
      window.sessionStorage.getItem(releaseDraftKey(projectId)),
      projectId,
    );
  } catch {
    storageAvailable = false;
  }
  try {
    releaseId = window.localStorage.getItem(releaseKey(projectId)) || undefined;
  } catch {
    /* The package may still be created in memory. */
  }
  const next: ReleaseDraftSnapshot = {
    version: 1,
    projectId,
    view: releaseId ? 'files' : 'form',
    ...draft,
    releaseId,
    error: null,
    storageAvailable,
  };
  snapshots.set(projectId, next);
  return next;
}

export function subscribeReleaseDraft(projectId: string, callback: () => void) {
  const set = listeners.get(projectId) ?? new Set<() => void>();
  set.add(callback);
  listeners.set(projectId, set);
  return () => {
    set.delete(callback);
    if (!set.size) listeners.delete(projectId);
  };
}

/** One synchronous store owns writes, including callbacks from an unmounted page. */
export function updateReleaseDraft(
  projectId: string,
  update: (current: ReleaseDraftSnapshot) => ReleaseDraftSnapshot,
) {
  const current = getReleaseDraftSnapshot(projectId);
  let next = update(current);
  if (next === current) return current;
  try {
    if (next.draft || next.submission) {
      const { version, projectId: owner, view, draft, submission } = next;
      window.sessionStorage.setItem(
        releaseDraftKey(projectId),
        JSON.stringify({ version, projectId: owner, view, draft, submission }),
      );
    } else window.sessionStorage.removeItem(releaseDraftKey(projectId));
    next = { ...next, storageAvailable: true };
  } catch {
    next = { ...next, storageAvailable: false };
  }
  if (next.releaseId !== current.releaseId) {
    try {
      if (next.releaseId)
        window.localStorage.setItem(releaseKey(projectId), next.releaseId);
      else if (
        window.localStorage.getItem(releaseKey(projectId)) === current.releaseId
      )
        window.localStorage.removeItem(releaseKey(projectId));
    } catch {
      /* The authoritative response remains available in memory. */
    }
  }
  snapshots.set(projectId, next);
  listeners.get(projectId)?.forEach((callback) => callback());
  return next;
}

/** Simulates a browser reload in tests; runtime navigation intentionally retains the store. */
export function resetReleaseDraftMemory() {
  snapshots.clear();
  listeners.clear();
}
