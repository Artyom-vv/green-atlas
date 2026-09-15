import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import { useForm } from 'react-hook-form';
import { emptyReleaseForm, type ReleaseDraft } from './releaseDraft';
import type { ReleaseFormValues } from './releaseForm';
import {
  getReleaseDraftSnapshot,
  updateReleaseDraft,
} from './releaseDraftStorage';

interface ReleaseDraftFormOptions {
  projectId: string;
  draft?: ReleaseDraft;
  initialHorizon: number;
  newDraft: () => ReleaseDraft;
}

function formValues(values: ReleaseFormValues): ReleaseFormValues {
  return {
    mode: values.mode,
    sceneHorizon: values.sceneHorizon,
    basis: { ...values.basis },
  };
}

const sameValues = (a: ReleaseFormValues, b: ReleaseFormValues) =>
  a.mode === b.mode &&
  a.sceneHorizon === b.sceneHorizon &&
  a.basis.pp616_status === b.basis.pp616_status &&
  a.basis.pp1160_status === b.basis.pp1160_status &&
  a.basis.pp616_reference === b.basis.pp616_reference &&
  a.basis.pp1160_reference === b.basis.pp1160_reference &&
  a.basis.confirmed_by === b.basis.confirmed_by;

/** RHF owns live fields. The store is a serializable checkpoint for navigation,
 * reload, and submission receipts; hydration never creates a user edit. */
export function useReleaseDraftForm({
  projectId,
  draft,
  initialHorizon,
  newDraft,
}: ReleaseDraftFormOptions) {
  const methods = useForm<ReleaseFormValues>({
    defaultValues: formValues(draft ?? emptyReleaseForm(initialHorizon)),
  });
  const { getValues, reset, subscribe } = methods;
  const hydrating = useRef(false);
  const owner = useRef(projectId);
  const checkpoint = useCallback(
    (values: ReleaseFormValues) => {
      updateReleaseDraft(projectId, (current) => {
        if (current.submission?.phase === 'pending') return current;
        if (current.draft && sameValues(current.draft, values)) return current;
        const currentDraft = current.draft ?? newDraft();
        return {
          ...current,
          view: 'form',
          draft: {
            ...currentDraft,
            ...formValues(values),
            revision: currentDraft.revision + 1,
          },
          error: null,
          notice: undefined,
        };
      });
    },
    [newDraft, projectId],
  );

  useLayoutEffect(() => {
    const target = formValues(draft ?? emptyReleaseForm(initialHorizon));
    if (owner.current !== projectId || !sameValues(getValues(), target)) {
      hydrating.current = true;
      reset(target);
      hydrating.current = false;
    }
    owner.current = projectId;
  }, [draft, getValues, initialHorizon, projectId, reset]);

  useEffect(
    () =>
      subscribe({
        formState: { values: true },
        callback: ({ values }) => {
          if (!hydrating.current && owner.current === projectId)
            checkpoint(values);
        },
      }),
    [checkpoint, projectId, subscribe],
  );

  const updateForm = useCallback(
    (values: ReleaseFormValues) => {
      if (getReleaseDraftSnapshot(projectId).submission?.phase === 'pending')
        return;
      checkpoint(values);
      hydrating.current = true;
      reset(formValues(values), { keepDefaultValues: true });
      hydrating.current = false;
    },
    [checkpoint, projectId, reset],
  );

  return { formMethods: methods, updateForm };
}
