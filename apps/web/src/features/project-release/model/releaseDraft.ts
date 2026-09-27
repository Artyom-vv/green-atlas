import type { ReleaseCreateRequest } from '@green/api-client';
import {
  pp616Choices,
  pp1160Choices,
  releaseFieldLimits,
  type ReleaseFormValues,
} from './releaseForm';
export type { ReleaseFormValues } from './releaseForm';

export type ReleaseDraftContext = {
  planId?: string;
  planVersion: number;
  geometryVersion: number;
};
export type ReleaseDraft = ReleaseFormValues & {
  id: string;
  revision: number;
  context: ReleaseDraftContext;
  editingReleaseId?: string;
};
export type ReleaseSubmission = {
  id: string;
  draftId: string;
  draftRevision: number;
  context: ReleaseDraftContext;
  request: ReleaseCreateRequest;
  phase: 'pending' | 'unknown';
};
export type ReleaseDraftDocument = {
  version: 1;
  projectId: string;
  view: 'form' | 'files';
  draft?: ReleaseDraft;
  submission?: ReleaseSubmission;
};

export function emptyReleaseForm(sceneHorizon = 0): ReleaseFormValues {
  return {
    mode: 'draft',
    sceneHorizon,
    basis: {
      pp616_status: 'pending',
      pp616_reference: '',
      pp1160_status: 'pending',
      pp1160_reference: '',
      confirmed_by: '',
    },
  };
}

export function sameReleaseContext(
  a: ReleaseDraftContext,
  b: ReleaseDraftContext,
) {
  return (
    a.planId === b.planId &&
    a.planVersion === b.planVersion &&
    a.geometryVersion === b.geometryVersion
  );
}

export function releaseDraftRequest(
  values: ReleaseFormValues,
): ReleaseCreateRequest {
  return {
    mode: values.mode,
    scene_horizon: values.sceneHorizon,
    ...(values.mode === 'final'
      ? { regulatory_basis: { ...values.basis } }
      : {}),
  };
}

const object = (value: unknown): Record<string, unknown> | undefined =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
const text = (value: unknown, max: number) =>
  typeof value === 'string' && value.length <= max;
const integer = (value: unknown, min: number, max = Number.MAX_SAFE_INTEGER) =>
  typeof value === 'number' &&
  Number.isSafeInteger(value) &&
  value >= min &&
  value <= max;
const member = (value: unknown, values: string[]) =>
  typeof value === 'string' && values.includes(value);
function validContext(value: unknown): value is ReleaseDraftContext {
  const item = object(value);
  return Boolean(
    item &&
    (item.planId === undefined || text(item.planId, 200)) &&
    integer(item.planVersion, 1) &&
    integer(item.geometryVersion, 0),
  );
}
function validValues(
  value: unknown,
): value is ReleaseFormValues & Record<string, unknown> {
  const item = object(value),
    basis = object(item?.basis);
  return Boolean(
    item &&
    member(item.mode, ['draft', 'final']) &&
    integer(item.sceneHorizon, 0, releaseFieldLimits.horizon) &&
    basis &&
    member(
      basis.pp616_status,
      pp616Choices.map((item) => item.value),
    ) &&
    member(
      basis.pp1160_status,
      pp1160Choices.map((item) => item.value),
    ) &&
    text(basis.pp616_reference, releaseFieldLimits.reference) &&
    text(basis.pp1160_reference, releaseFieldLimits.reference) &&
    text(basis.confirmed_by, releaseFieldLimits.reviewer),
  );
}

function validDraft(value: unknown): value is ReleaseDraft {
  const draft = object(value);
  return Boolean(
    draft &&
    validValues(draft) &&
    text(draft.id, 200) &&
    draft.id &&
    integer(draft.revision, 0) &&
    validContext(draft.context) &&
    (draft.editingReleaseId === undefined || text(draft.editingReleaseId, 200)),
  );
}

/** Browser notes are untrusted input and never stand in for a server package. */
export function parseReleaseDraftDocument(
  raw: string | null,
  projectId: string,
): ReleaseDraftDocument | undefined {
  if (!raw || raw.length > 12_000) return undefined;
  try {
    const doc = object(JSON.parse(raw));
    if (
      !doc ||
      doc.version !== 1 ||
      doc.projectId !== projectId ||
      !member(doc.view, ['form', 'files'])
    )
      return undefined;
    const draft = doc.draft;
    if (draft !== undefined && !validDraft(draft)) return undefined;
    const submission = object(doc.submission);
    if (
      submission &&
      (!draft ||
        submission.draftId !== draft.id ||
        !integer(submission.draftRevision, 0) ||
        !text(submission.id, 200) ||
        !submission.id ||
        !validContext(submission.context) ||
        !member(submission.phase, ['pending', 'unknown']))
    )
      return undefined;
    if (doc.submission !== undefined && !submission) return undefined;
    // Only local form fields survive reload. An interrupted POST is never replayed.
    return {
      version: 1,
      projectId,
      view: doc.view as 'form' | 'files',
      draft,
      submission: submission
        ? ({
            ...submission,
            request: releaseDraftRequest(draft!),
            phase: 'unknown',
          } as ReleaseSubmission)
        : undefined,
    };
  } catch {
    return undefined;
  }
}
