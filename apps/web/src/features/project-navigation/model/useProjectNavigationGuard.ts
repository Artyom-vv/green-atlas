import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { useBlocker } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { desktopBridge } from '@/shared/desktop/desktopBridge';
import { useLocalRuntime } from '@/shared/desktop/useLocalRuntime';
import type {
  DesktopNavigationRequest,
  WriteOutcome,
} from '@/shared/desktop/protocol';

export interface ProjectNavigationOptions {
  projectId?: string;
  blocksLeaving: boolean;
  hasUnsavedWork: boolean;
  preserveOperation?: boolean;
  writeOutcome?: WriteOutcome;
  readRecovery?: boolean;
  editingBlocked?: boolean;
}

export function useProjectNavigationGuard({
  projectId,
  blocksLeaving,
  hasUnsavedWork,
  preserveOperation = false,
  writeOutcome = 'idle',
  readRecovery = false,
  editingBlocked = false,
}: ProjectNavigationOptions) {
  const client = useQueryClient();
  const runtimeAvailable = useLocalRuntime();
  const [nativeRequest, setNativeRequest] =
    useState<DesktopNavigationRequest>();
  const pendingNative = useRef<DesktopNavigationRequest | undefined>(undefined);
  useEffect(() => {
    if (runtimeAvailable) return;
    pendingNative.current = undefined;
    setNativeRequest(undefined);
  }, [runtimeAvailable]);
  const prepareLeaving = useCallback(async () => {
    await Promise.all(
      (projectId
        ? [
            ['workspace-project', projectId],
            ['setup-project', projectId],
            ['plan-history', projectId],
            ['projects'],
          ]
        : [['projects']]
      ).map((queryKey) =>
        client.invalidateQueries({ queryKey, refetchType: 'none' }),
      ),
    );
  }, [client, projectId]);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      (blocksLeaving ||
        (preserveOperation &&
          !nextLocation.pathname.startsWith(`/projects/${projectId}/`))) &&
      currentLocation.pathname !== nextLocation.pathname,
  );
  const warnBeforeUnload = blocksLeaving || hasUnsavedWork;
  useEffect(() => {
    if (!warnBeforeUnload) return;
    const preventLoss = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', preventLoss);
    return () => window.removeEventListener('beforeunload', preventLoss);
  }, [warnBeforeUnload]);

  useLayoutEffect(
    () =>
      desktopBridge.register((request) => {
        if (blocksLeaving || preserveOperation || hasUnsavedWork) {
          pendingNative.current = request;
          setNativeRequest(request);
        } else {
          void prepareLeaving().then(() =>
            desktopBridge.decision(request, true),
          );
        }
      }),
    [blocksLeaving, preserveOperation, hasUnsavedWork, prepareLeaving],
  );
  useLayoutEffect(() => {
    desktopBridge.updateState({
      hasDraft: hasUnsavedWork,
      writeOutcome,
      readRecovery,
      editingBlocked,
    });
  }, [hasUnsavedWork, writeOutcome, readRecovery, editingBlocked]);
  useEffect(
    () => () => {
      const request = pendingNative.current;
      if (request) desktopBridge.decision(request, false);
      void prepareLeaving();
    },
    [prepareLeaving],
  );
  useEffect(() => {
    const request = pendingNative.current;
    if (!request || blocksLeaving || preserveOperation || hasUnsavedWork)
      return;
    void prepareLeaving().then(() => {
      if (pendingNative.current !== request) return;
      desktopBridge.decision(request, true);
      pendingNative.current = undefined;
      setNativeRequest(undefined);
    });
  }, [blocksLeaving, preserveOperation, hasUnsavedWork, prepareLeaving]);

  const reset = useCallback(() => {
    const request = pendingNative.current;
    if (request) desktopBridge.decision(request, false);
    pendingNative.current = undefined;
    setNativeRequest(undefined);
    blocker.reset?.();
  }, [blocker]);
  const proceed = useCallback(() => {
    if (writeOutcome === 'writing' || writeOutcome === 'unknown') return;
    const request = pendingNative.current;
    void prepareLeaving().then(() => {
      if (request) {
        if (pendingNative.current !== request) return;
        desktopBridge.decision(request, true);
        pendingNative.current = undefined;
        setNativeRequest(undefined);
      } else blocker.proceed?.();
    });
  }, [blocker, prepareLeaving, writeOutcome]);
  // A blocked native action uses the same surface and explicit discard as SPA navigation.
  return nativeRequest
    ? { state: 'blocked' as const, reset, proceed }
    : { ...blocker, reset, proceed };
}
