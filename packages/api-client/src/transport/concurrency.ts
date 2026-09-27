const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

// These POST endpoints calculate or record workflow progress without applying
// a project edit. Their responses must not advance the editor's snapshot.
const NON_PROJECT_MUTATIONS = [
  /\/conversations(?:\/|$)/,
  /\/agent-runs(?:\/|$)/,
  /\/operations(?:\/|$)/,
  /\/plan\/(?:placement-check|change-sets\/preview|patterns\/preview|recommendations\/preview|brush\/preview)$/,
  /\/building-screen\/preview$/,
  /\/species\/shortlist$/,
];

export interface RequestScope {
  projectId?: string;
  sendsProjectVersion: boolean;
  receivesProjectVersion: boolean;
}

export function requestScope(path: string, method = 'GET'): RequestScope {
  const pathname = path.split('?')[0];
  const match = pathname.match(/^\/api\/projects\/([^/]+)/);
  const projectId = match?.[1] ? decodeURIComponent(match[1]) : undefined;
  const verb = method.toUpperCase();
  const isMutation =
    !SAFE_METHODS.has(verb) &&
    !NON_PROJECT_MUTATIONS.some((pattern) => pattern.test(pathname));
  const isProjectRead =
    verb === 'GET' && /^\/api\/projects\/[^/]+$/.test(pathname);
  // Confirmation uses the saved preview and conversation revision rather than
  // If-Match, but its successful response still commits project state.
  const isConversationConfirmation =
    verb === 'POST' &&
    /\/conversations\/[^/]+\/proposals\/[^/]+\/confirm$/.test(pathname);

  return {
    projectId,
    sendsProjectVersion: Boolean(projectId && isMutation),
    receivesProjectVersion: Boolean(
      projectId && (isProjectRead || isMutation || isConversationConfirmation),
    ),
  };
}

function positiveVersion(value: unknown): number | undefined {
  const version = typeof value === 'string' ? Number(value) : value;
  return typeof version === 'number' && Number.isInteger(version) && version > 0
    ? version
    : undefined;
}

export class ProjectVersions {
  private readonly versions = new Map<string, number>();

  headers(scope: RequestScope, source?: HeadersInit): Headers {
    const headers = new Headers(source);
    const version = scope.projectId && this.versions.get(scope.projectId);
    if (scope.sendsProjectVersion && version && !headers.has('If-Match')) {
      headers.set('If-Match', `"${version}"`);
    }
    return headers;
  }

  observe(scope: RequestScope, headers: Headers, payload: unknown): void {
    if (scope.projectId && !scope.receivesProjectVersion) return;

    const responseVersion = positiveVersion(
      headers.get('X-Project-State-Version'),
    );
    if (scope.projectId && responseVersion !== undefined) {
      this.versions.set(scope.projectId, responseVersion);
    }

    const items = Array.isArray(payload) ? payload : [payload];
    for (const item of items) {
      if (!item || typeof item !== 'object') continue;
      const candidate = item as { id?: unknown; state_version?: unknown };
      const version = positiveVersion(candidate.state_version);
      if (typeof candidate.id === 'string' && version !== undefined) {
        this.versions.set(candidate.id, version);
      }
    }
  }
}
