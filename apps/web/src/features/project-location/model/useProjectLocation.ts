import {
  api,
  ApiClientError,
  type LocationAlignment,
  type LocationControlPair,
  type LocationDraft,
  type LocationPoint,
  type LocationResolveRequest,
  type LocationServiceSettings,
  type LocationState,
  type LocationSuggestion,
} from '@green/api-client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

export const locationActions = {
  read: api.getProjectLocation,
  suggest: api.suggestProjectLocation,
  resolve: api.resolveProjectLocation,
  align: api.alignProjectLocation,
  apply: api.applyProjectLocation,
};
export interface DraftPair {
  cad_xy?: LocationPoint;
  lon_lat?: LocationPoint;
  usage: 'fit' | 'holdout';
}
export type LocationScreen =
  | 'loading'
  | 'place'
  | 'resolving'
  | 'aligning'
  | 'review'
  | 'manual'
  | 'street'
  | 'center'
  | 'error';
export const emptyLocationPairs = (): DraftPair[] =>
  Array.from({ length: 4 }, (_, index) => ({
    usage: index === 3 ? 'holdout' : 'fit',
  }));
export function completePairs(pairs: DraftPair[]): LocationControlPair[] {
  return pairs.filter((pair): pair is LocationControlPair =>
    Boolean(pair.cad_xy && pair.lon_lat),
  );
}
export function canFitManually(pairs: DraftPair[]) {
  const complete = completePairs(pairs);
  return (
    complete.filter((pair) => pair.usage === 'fit').length >= 3 &&
    complete.some((pair) => pair.usage === 'holdout')
  );
}
const messageOf = (error: unknown) =>
  error instanceof Error
    ? error.message.replace(/[.!]$/, '')
    : 'Источник недоступен';
const isConflict = (error: unknown) =>
  error instanceof ApiClientError &&
  /CONFLICT|STALE|SOURCE|REVISION|GEOMETRY_VERSION/.test(
    error.code.toUpperCase(),
  );
const tuple = (value: unknown, size: number) =>
  Array.isArray(value) &&
  value.length === size &&
  value.every(
    (number) => typeof number === 'number' && Number.isFinite(number),
  );
function requestOf(draft: LocationDraft): LocationResolveRequest {
  const center = draft.provenance?.requested_map_center_lon_lat;
  const section = draft.provenance?.requested_section_bbox;
  return {
    suggestion: draft.suggestion,
    street_candidate_id: draft.selected_street_id ?? undefined,
    center_lon_lat: tuple(center, 2) ? (center as LocationPoint) : undefined,
    section_bbox: tuple(section, 4)
      ? (section as NonNullable<LocationResolveRequest['section_bbox']>)
      : undefined,
  };
}

export function useProjectLocation(
  projectId: string,
  onApplied: () => void | Promise<void>,
) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<LocationState>();
  const [draft, setDraft] = useState<LocationDraft>();
  const [alignment, setAlignment] = useState<LocationAlignment>();
  const [suggestion, setSuggestion] = useState<LocationSuggestion>();
  const [pairs, setPairs] = useState<DraftPair[]>(emptyLocationPairs);
  const [activePair, setActivePair] = useState(0);
  const [busy, setBusy] = useState<
    'loading' | 'resolving' | 'aligning' | 'applying' | undefined
  >('loading');
  const [screen, setScreen] = useState<LocationScreen>('loading');
  const [error, setError] = useState<string>();
  const [applied, setApplied] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [stale, setStale] = useState(false);
  const [serviceConfigurationRevision, setServiceConfigurationRevision] =
    useState(0);
  const updateServiceSettings = useCallback(
    (settings: LocationServiceSettings) => {
      setState((previous) =>
        previous
          ? { ...previous, configured: settings.configured, message: null }
          : previous,
      );
      setServiceConfigurationRevision((revision) => revision + 1);
    },
    [],
  );
  const lastRequest = useRef<LocationResolveRequest | undefined>(undefined);
  const reviewed = useRef<LocationAlignment | undefined>(undefined);
  const request = useRef<{ generation: number; controller?: AbortController }>({
    generation: 0,
  });
  const invalidate = useCallback(() => {
    request.current.controller?.abort();
    request.current = { generation: request.current.generation + 1 };
    return request.current.generation;
  }, []);
  const begin = useCallback(() => {
    const generation = invalidate(),
      controller = new AbortController();
    request.current.controller = controller;
    setError(undefined);
    setStale(false);
    return { generation, signal: controller.signal };
  }, [invalidate]);
  const current = useCallback(
    (generation: number) => request.current.generation === generation,
    [],
  );
  const resetPairs = useCallback(() => {
    setPairs(emptyLocationPairs());
    setActivePair(0);
    setDirty(false);
  }, []);
  const load = useCallback(async () => {
    const run = begin();
    setBusy('loading');
    setScreen('loading');
    try {
      const result = await locationActions.read(projectId, run.signal);
      if (!current(run.generation)) return;
      setState(result);
      setDraft(result.draft ?? undefined);
      setSuggestion(result.draft?.suggestion);
      resetPairs();
      setAlignment(result.alignment ?? undefined);
      setApplied(
        Boolean(
          result.applied &&
          !['stale', 'invalid'].includes(result.applied.status) &&
          result.draft &&
          result.alignment &&
          result.applied.draft_id === result.draft.id &&
          result.applied.alignment_id === result.alignment.id &&
          result.alignment.draft_id === result.draft.id &&
          result.applied.source_sha256 === result.draft.source_sha256,
        ),
      );
      reviewed.current =
        result.alignment?.status === 'candidate' ? result.alignment : undefined;
      if (result.draft) lastRequest.current = requestOf(result.draft);
      if (
        result.draft?.status === 'ready' &&
        result.alignment?.draft_id === result.draft.id
      ) {
        if (result.alignment.status === 'candidate') setScreen('review');
        else setScreen('manual');
      } else if (result.draft?.status === 'ready') {
        setBusy('aligning');
        setScreen('aligning');
        const fit = await locationActions.align(
          projectId,
          { draft_id: result.draft.id, control_pairs: [] },
          run.signal,
        );
        if (!current(run.generation)) return;
        setAlignment(fit);
        reviewed.current = fit.status === 'candidate' ? fit : undefined;
        setScreen(fit.status === 'candidate' ? 'review' : 'manual');
      } else if (result.draft?.status === 'no_coordinates') setScreen('center');
      else if (result.draft && result.draft.street_candidates.length)
        setScreen('street');
      else setScreen('place');
    } catch (error) {
      if (current(run.generation)) {
        setError(messageOf(error));
        setScreen('error');
      }
    } finally {
      if (current(run.generation)) setBusy(undefined);
    }
  }, [begin, current, projectId, resetPairs]);
  useEffect(() => {
    void load();
    return () => {
      invalidate();
    };
  }, [load, invalidate]);

  const showSearch = () => {
    invalidate();
    setBusy(undefined);
    setError(undefined);
    setScreen('place');
  };
  const resetLocation = () => {
    invalidate();
    setBusy(undefined);
    setError(undefined);
    setDraft(undefined);
    setAlignment(undefined);
    setSuggestion(undefined);
    setApplied(false);
    resetPairs();
    reviewed.current = undefined;
    lastRequest.current = undefined;
    setScreen('place');
  };
  const resolve = async (body: LocationResolveRequest) => {
    const run = begin();
    setBusy('resolving');
    setScreen('resolving');
    setApplied(false);
    resetPairs();
    reviewed.current = undefined;
    setAlignment(undefined);
    setDraft(undefined);
    setSuggestion(body.suggestion);
    lastRequest.current = body;
    try {
      const result = await locationActions.resolve(projectId, body, run.signal);
      if (!current(run.generation)) return;
      setDraft(result);
      setSuggestion(result.suggestion);
      if (result.status !== 'ready') {
        if (result.status === 'no_coordinates') setScreen('center');
        else if (result.street_candidates.length) setScreen('street');
        else {
          setScreen('error');
          setError(result.message.replace(/[.!]$/, ''));
        }
        return;
      }
      setBusy('aligning');
      setScreen('aligning');
      const fit = await locationActions.align(
        projectId,
        { draft_id: result.id, control_pairs: [] },
        run.signal,
      );
      if (!current(run.generation)) return;
      setAlignment(fit);
      reviewed.current = fit.status === 'candidate' ? fit : undefined;
      setScreen(fit.status === 'candidate' ? 'review' : 'manual');
    } catch (error) {
      if (current(run.generation)) {
        setError(messageOf(error));
        setScreen('error');
        setStale(isConflict(error));
      }
    } finally {
      if (current(run.generation)) setBusy(undefined);
    }
  };
  const changePairs = (next: DraftPair[], selected = activePair) => {
    invalidate();
    setBusy(undefined);
    setError(undefined);
    setAlignment(undefined);
    setApplied(false);
    setDirty(true);
    setPairs(next);
    setActivePair(selected);
  };
  const placePoint = (side: 'cad_xy' | 'lon_lat', point: LocationPoint) => {
    if (busy) return;
    changePairs(
      pairs.map((pair, index) =>
        index === activePair ? { ...pair, [side]: point } : pair,
      ),
    );
  };
  const editPoint = (side: 'cad_xy' | 'lon_lat') => {
    changePairs(
      pairs.map((pair, index) =>
        index !== activePair
          ? pair
          : side === 'cad_xy'
            ? { usage: pair.usage }
            : { cad_xy: pair.cad_xy, usage: pair.usage },
      ),
    );
  };
  const clearPair = () =>
    changePairs(
      pairs.map((pair, index) =>
        index === activePair ? { usage: pair.usage } : pair,
      ),
    );
  const nextPair = () => {
    const index = pairs.findIndex(
      (pair, index) => index > activePair && (!pair.cad_xy || !pair.lon_lat),
    );
    const first = pairs.findIndex((pair) => !pair.cad_xy || !pair.lon_lat);
    if (index >= 0) setActivePair(index);
    else if (first >= 0) setActivePair(first);
    setError(undefined);
  };
  const startManual = () => {
    if (alignment?.status === 'candidate') reviewed.current = alignment;
    if (
      !dirty &&
      alignment?.method === 'manual' &&
      alignment.control_pairs.length === 4
    ) {
      setPairs([
        ...alignment.control_pairs.filter((pair) => pair.usage === 'fit'),
        ...alignment.control_pairs.filter((pair) => pair.usage === 'holdout'),
      ]);
      setActivePair(0);
    }
    setScreen('manual');
    setError(undefined);
  };
  const discardManual = () => {
    resetPairs();
    setError(undefined);
    setAlignment(reviewed.current);
    setScreen(reviewed.current ? 'review' : 'place');
  };
  const align = async (manual: boolean) => {
    if (
      !draft ||
      draft.status !== 'ready' ||
      (manual && !canFitManually(pairs))
    )
      return false;
    const run = begin();
    setBusy('aligning');
    if (!manual) setScreen('aligning');
    try {
      const fit = await locationActions.align(
        projectId,
        {
          draft_id: draft.id,
          control_pairs: manual ? completePairs(pairs) : [],
        },
        run.signal,
      );
      if (!current(run.generation)) return false;
      setAlignment(fit);
      if (fit.status === 'candidate') {
        reviewed.current = fit;
        setScreen('review');
        return true;
      }
      setScreen('manual');
      setError(manual ? fit.message.replace(/[.!]$/, '') : undefined);
      return false;
    } catch (error) {
      if (current(run.generation)) {
        setError(messageOf(error));
        setStale(isConflict(error));
      }
      return false;
    } finally {
      if (current(run.generation)) setBusy(undefined);
    }
  };
  const apply = async () => {
    if (!draft || alignment?.status !== 'candidate') return false;
    const run = begin();
    setBusy('applying');
    try {
      const result = await locationActions.apply(
        projectId,
        {
          draft_id: draft.id,
          alignment_id: alignment.id,
          source_sha256: draft.source_sha256,
          geometry_version: draft.geometry_version,
        },
        run.signal,
      );
      if (!current(run.generation)) return false;
      if (
        result.applied?.alignment_id !== alignment.id ||
        result.applied.draft_id !== draft.id ||
        result.applied.source_sha256 !== draft.source_sha256 ||
        result.applied.geometry_version !== draft.geometry_version ||
        ['stale', 'invalid'].includes(result.applied.status)
      ) {
        throw new ApiClientError(
          'LOCATION_APPLY_UNCONFIRMED',
          'Применение привязки не подтверждено',
        );
      }
      setState(result);
      setApplied(true);
      setDirty(false);
      await Promise.all(
        ['plan-scene', 'scene-preparation'].map((key) =>
          queryClient.invalidateQueries({
            queryKey: [key, projectId],
            refetchType: 'none',
          }),
        ),
      );
      try {
        await onApplied();
      } catch {
        setError('Привязка сохранена, обновление окружения недоступно');
      }
      return current(run.generation);
    } catch (error) {
      if (current(run.generation)) {
        setError(messageOf(error));
        setStale(isConflict(error));
        if (isConflict(error)) {
          setAlignment(undefined);
          reviewed.current = undefined;
        }
      }
      return false;
    } finally {
      if (current(run.generation)) setBusy(undefined);
    }
  };
  return {
    state,
    draft,
    alignment,
    suggestion,
    pairs,
    activePair,
    busy,
    screen,
    error,
    applied,
    stale,
    serviceConfigurationRevision,
    updateServiceSettings,
    hasUnsavedChanges: dirty,
    lastRequest: lastRequest.current,
    hasReview: reviewed.current?.status === 'candidate',
    load,
    resetLocation,
    resolve,
    changePairs,
    placePoint,
    editPoint,
    clearPair,
    nextPair,
    setActivePair,
    showSearch,
    startManual,
    discardManual,
    align,
    apply,
    canFit: canFitManually(pairs),
  };
}
