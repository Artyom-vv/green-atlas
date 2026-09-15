import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type { PlacementCheck, Plan } from '@green/api-client';
import {
  checkPlacementCandidate,
  addPlacementCandidate,
} from './api/singlePlacement';
import {
  placementCandidate,
  sameCoordinate,
  placementIntentKey as intentKey,
  placementContextKey as contextKey,
  stalePlacementBasis as staleBasis,
  placementSavedNotice as savedNotice,
  placementRefreshFailure as refreshFailure,
  stalePlacementError as staleError,
  placementVersionError as versionError,
  placementErrorFrom as errorFrom,
  type Coordinate,
  type SinglePlacementContext,
} from './model/singlePlacement';

export interface SinglePlacementOptions extends SinglePlacementContext {
  /** Other manual work only; do not feed this hook's checking/placing back here. */
  disabled?: boolean;
  onApplied?: (plan: Plan) => void | Promise<void>;
  /** Reload the project after a conflict or a failed post-commit refresh. */
  onRefresh?: () => void | Promise<void>;
}

export type SinglePlacementResult = {
  status:
    'applied' | 'blocked' | 'stale' | 'busy' | 'missing-species' | 'error';
  check?: PlacementCheck;
  plan?: Plan;
  error?: Error;
};

type CheckState = {
  key: string;
  coordinate: Coordinate;
  value: PlacementCheck;
};
type PlaceRequest = {
  key: string;
  controller: AbortController;
  cancelled: boolean;
  writing: boolean;
};
type Recovery = { projectId: string; error: Error; afterCommit: boolean };
type SavedPosition = { intent: string; coordinate: Coordinate };
/** Read-only hover and an explicit click intent share candidate parameters.
 * A click always checks its own coordinate immediately. Cursor motion can
 * supersede a hover, but cannot redirect that click to a different position.
 */
export function useSinglePlacement(options: SinglePlacementOptions) {
  const latest = useRef(options);
  useLayoutEffect(() => {
    latest.current = options;
  }, [options]);
  const key = contextKey(options);
  const mounted = useRef(true);
  const cursorRef = useRef<Coordinate | undefined>(undefined);
  const hoverSequence = useRef(0);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const hoverController = useRef<AbortController | undefined>(undefined);
  const pendingPlace = useRef<PlaceRequest | undefined>(undefined);
  const recoveryRef = useRef<Recovery | undefined>(undefined);
  const savedPosition = useRef<SavedPosition | undefined>(undefined);
  const refreshRequest = useRef<string | undefined>(undefined);
  const [cursor, setCursor] = useState<Coordinate>();
  const [checked, setChecked] = useState<CheckState>();
  const [failure, setFailure] = useState<{ key: string; value: Error }>();
  const [hoverPending, setHoverPending] = useState<string>();
  const [clickChecking, setClickChecking] = useState<string>();
  const [placePending, setPlacePending] = useState<PlaceRequest>();
  const [recovery, setRecovery] = useState<Recovery>();
  const [noticeIntent, setNoticeIntent] = useState<string>();
  const [refreshingProject, setRefreshingProject] = useState<string>();

  const stopHover = useCallback(() => {
    // Invalidate synchronously, before the next debounce can start.
    hoverSequence.current += 1;
    clearTimeout(hoverTimer.current);
    hoverController.current?.abort();
    hoverController.current = undefined;
    if (mounted.current) setHoverPending(undefined);
  }, []);

  const stopUnsentPlace = useCallback(() => {
    const pending = pendingPlace.current;
    if (!pending || pending.writing) return;
    pending.cancelled = true;
    pending.controller.abort();
    pendingPlace.current = undefined;
    if (mounted.current) {
      setPlacePending(undefined);
      setClickChecking(undefined);
    }
  }, []);

  const requireRefresh = useCallback(
    (projectId: string, error: Error, afterCommit = false) => {
      if (!mounted.current || latest.current.projectId !== projectId) return;
      const next = { projectId, error, afterCommit };
      recoveryRef.current = next;
      stopHover();
      setChecked(undefined);
      setRecovery(next);
    },
    [stopHover],
  );

  const forgetSavedPosition = useCallback(() => {
    savedPosition.current = undefined;
    setNoticeIntent(undefined);
  }, []);

  const hover = useCallback(
    (pointer?: Coordinate) => {
      const o = latest.current;
      // Select/pan also report pointer motion. Only an active placement tool
      // owns a cursor; otherwise new arrays trigger workspace-wide renders.
      const coordinate = o.active ? pointer : undefined;
      stopHover();
      cursorRef.current = coordinate ? [...coordinate] : undefined;
      setCursor(cursorRef.current);
      setChecked(undefined);
      setFailure(undefined);
      const saved = savedPosition.current;
      if (
        saved &&
        (saved.intent !== intentKey(o) ||
          (coordinate && !sameCoordinate(saved.coordinate, coordinate)))
      )
        forgetSavedPosition();
      if (
        recoveryRef.current?.projectId === o.projectId ||
        refreshRequest.current === o.projectId
      )
        return;
      if (
        coordinate &&
        savedPosition.current?.intent === intentKey(o) &&
        sameCoordinate(savedPosition.current.coordinate, coordinate)
      )
        return;
      if (
        !coordinate ||
        !o.active ||
        !o.kind ||
        o.planVersion === undefined ||
        !o.speciesRevisionId ||
        o.disabled
      )
        return;
      const point: Coordinate = [...coordinate];
      const candidate = placementCandidate(o, point);
      if (!candidate) return;
      const requestKey = contextKey(o);
      const sequence = hoverSequence.current;
      setHoverPending(requestKey);
      hoverTimer.current = setTimeout(() => {
        const controller = new AbortController();
        hoverController.current = controller;
        const current = () =>
          mounted.current &&
          sequence === hoverSequence.current &&
          requestKey === contextKey(latest.current) &&
          !latest.current.disabled;
        void checkPlacementCandidate(candidate, controller.signal)
          .then((value) => {
            if (!current()) return;
            if (staleBasis(value, o)) {
              requireRefresh(o.projectId, staleError());
              return;
            }
            setChecked({ key: requestKey, coordinate: point, value });
          })
          .catch((error) => {
            if (current() && !controller.signal.aborted) {
              const failure = errorFrom(error);
              if (versionError(failure)) requireRefresh(o.projectId, failure);
              else setFailure({ key: requestKey, value: failure });
            }
          })
          .finally(() => {
            if (current()) setHoverPending(undefined);
          });
      }, 120);
    },
    [forgetSavedPosition, requireRefresh, stopHover],
  );

  const clear = useCallback(() => {
    stopHover();
    stopUnsentPlace();
    cursorRef.current = undefined;
    setCursor(undefined);
    setChecked(undefined);
    setFailure(undefined);
    forgetSavedPosition();
    // A tool change or Escape does not repair a stale project snapshot.
  }, [forgetSavedPosition, stopHover, stopUnsentPlace]);

  const place = useCallback(
    async (coordinate: Coordinate): Promise<SinglePlacementResult> => {
      const o = latest.current;
      if (
        pendingPlace.current ||
        o.disabled ||
        recoveryRef.current?.projectId === o.projectId ||
        refreshRequest.current === o.projectId
      )
        return { status: 'busy' };
      if (!o.active || !o.kind || o.planVersion === undefined)
        return { status: 'stale' };
      if (!o.speciesRevisionId) return { status: 'missing-species' };
      if (
        savedPosition.current?.intent === intentKey(o) &&
        sameCoordinate(savedPosition.current.coordinate, coordinate)
      )
        return { status: 'busy' };
      forgetSavedPosition();
      const point: Coordinate = [...coordinate];
      const requestKey = contextKey(o);
      const pending: PlaceRequest = {
        key: requestKey,
        controller: new AbortController(),
        cancelled: false,
        writing: false,
      };
      const current = () =>
        mounted.current &&
        !pending.cancelled &&
        requestKey === contextKey(latest.current) &&
        !latest.current.disabled &&
        recoveryRef.current?.projectId !== o.projectId;
      const atCursor = () => sameCoordinate(cursorRef.current, point);
      const candidate = placementCandidate(o, point);
      if (!candidate) return { status: 'stale' };
      stopHover();
      pendingPlace.current = pending;
      cursorRef.current = point;
      setCursor(point);
      setChecked(undefined);
      setFailure(undefined);
      setPlacePending(pending);
      setClickChecking(requestKey);
      let appliedPlan: Plan | undefined;
      try {
        const check = await checkPlacementCandidate(
          candidate,
          pending.controller.signal,
        );
        if (!current()) return { status: 'stale' };
        if (staleBasis(check, o)) {
          const error = staleError();
          requireRefresh(o.projectId, error);
          return { status: 'stale', check, error };
        }
        if (atCursor())
          setChecked({ key: requestKey, coordinate: point, value: check });
        setClickChecking(undefined);
        if (!check.allowed) return { status: 'blocked', check };
        // Once sent, do not abort a write: the server may already have committed.
        // Frozen If-Match protects against another edit between check and Add.
        pending.writing = true;
        const plan = await addPlacementCandidate(candidate);
        appliedPlan = plan;
        if (mounted.current && o.projectId === latest.current.projectId) {
          stopHover();
          setChecked(undefined);
          if (intentKey(o) === intentKey(latest.current) && atCursor()) {
            savedPosition.current = { intent: intentKey(o), coordinate: point };
            setNoticeIntent(intentKey(o));
          }
        }
        await o.onApplied?.(plan);
        if (mounted.current && o.projectId === latest.current.projectId) {
          recoveryRef.current = undefined;
          setRecovery(undefined);
        }
        return { status: 'applied', check, plan };
      } catch (caught) {
        const error = appliedPlan
          ? refreshFailure(errorFrom(caught))
          : errorFrom(caught);
        if (!pending.writing && !current()) return { status: 'stale' };
        if (appliedPlan || versionError(error))
          requireRefresh(o.projectId, error, !!appliedPlan);
        else if (mounted.current && requestKey === contextKey(latest.current)) {
          setChecked(undefined);
          setFailure({ key: requestKey, value: error });
        }
        // A refresh failure must not describe an already committed Add as a
        // failed placement, which could invite an accidental duplicate retry.
        return appliedPlan
          ? { status: 'applied', plan: appliedPlan, error }
          : { status: versionError(error) ? 'stale' : 'error', error };
      } finally {
        if (pendingPlace.current === pending) {
          pendingPlace.current = undefined;
          if (mounted.current) {
            setPlacePending(undefined);
            setClickChecking(undefined);
          }
        }
      }
    },
    [forgetSavedPosition, requireRefresh, stopHover],
  );

  const retryRefresh = useCallback(async (): Promise<void> => {
    const o = latest.current;
    const recovery = recoveryRef.current;
    if (
      !recovery ||
      recovery.projectId !== o.projectId ||
      refreshRequest.current ||
      pendingPlace.current ||
      !o.onRefresh
    )
      return;
    refreshRequest.current = o.projectId;
    setRefreshingProject(o.projectId);
    stopHover();
    try {
      await o.onRefresh();
      if (!mounted.current || latest.current.projectId !== o.projectId) return;
      recoveryRef.current = undefined;
      setRecovery(undefined);
      setFailure(undefined);
      setChecked(undefined);
      setNoticeIntent(undefined);
      // Start the next candidate with a fresh pointer event. Keep the saved
      // position suppressed if the pointer returns to that same planting.
      cursorRef.current = undefined;
      setCursor(undefined);
    } catch (caught) {
      requireRefresh(
        o.projectId,
        recovery.afterCommit
          ? refreshFailure(errorFrom(caught))
          : errorFrom(caught),
        recovery.afterCommit,
      );
    } finally {
      refreshRequest.current = undefined;
      if (mounted.current) setRefreshingProject(undefined);
    }
  }, [requireRefresh, stopHover]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stopHover();
      stopUnsentPlace();
    };
  }, [stopHover, stopUnsentPlace]);

  useEffect(() => {
    stopUnsentPlace();
    if (
      recoveryRef.current &&
      recoveryRef.current.projectId !== latest.current.projectId
    ) {
      recoveryRef.current = undefined;
      setRecovery(undefined);
    }
    if (!latest.current.active) clear();
    else hover(cursorRef.current);
  }, [key, options.disabled, clear, hover, stopUnsentPlace]);

  const needsRefresh = recovery?.projectId === options.projectId;
  return {
    cursor,
    check:
      checked?.key === key &&
      cursor &&
      sameCoordinate(checked.coordinate, cursor)
        ? checked.value
        : undefined,
    error: needsRefresh
      ? recovery.error
      : failure?.key === key
        ? failure.value
        : undefined,
    notice:
      !needsRefresh && noticeIntent === intentKey(options)
        ? savedNotice
        : undefined,
    needsRefresh,
    refreshing: refreshingProject === options.projectId,
    checking: hoverPending === key || clickChecking === key,
    // Includes click-check and refresh, to keep one explicit placement in flight.
    placing: !!placePending,
    hover,
    clear,
    place,
    retryRefresh,
  };
}
