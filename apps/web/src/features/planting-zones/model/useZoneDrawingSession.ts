import { useCallback, useRef, useState } from 'react';
import type { ZoneDrawingSession, ZoneReviewDraft } from './zoneDrawing';

export interface ZoneDrawingSessionOptions {
  abortDrawing: () => void;
  onToolChange: (tool: 'draw_area' | 'select') => void;
  onReturn: (session: ZoneDrawingSession) => void;
}

/** Owns the transient contour gesture, never the saved or reviewed geometry. */
export function useZoneDrawingSession({
  abortDrawing,
  onToolChange,
  onReturn,
}: ZoneDrawingSessionOptions) {
  const [session, setSession] = useState<ZoneDrawingSession>();
  const activeSession = useRef<ZoneDrawingSession | undefined>(undefined);

  const begin = useCallback(
    (next: ZoneDrawingSession) => {
      if (activeSession.current) abortDrawing();
      activeSession.current = next;
      setSession(next);
      onToolChange('draw_area');
    },
    [abortDrawing, onToolChange],
  );

  const finish = useCallback(
    (expected: ZoneDrawingSession) => {
      if (activeSession.current !== expected) return;
      activeSession.current = undefined;
      setSession(undefined);
      onToolChange('select');
      return expected;
    },
    [onToolChange],
  );

  const redraw = useCallback(
    (review: ZoneReviewDraft) =>
      begin({ ...review, target: review.zone.id ?? 'new' }),
    [begin],
  );

  const discard = useCallback(() => {
    const cancelled = activeSession.current;
    if (!cancelled) return;
    activeSession.current = undefined;
    abortDrawing();
    setSession(undefined);
    onToolChange('select');
    return cancelled;
  }, [abortDrawing, onToolChange]);

  const cancel = useCallback(() => {
    const cancelled = discard();
    if (cancelled) onReturn(cancelled);
  }, [discard, onReturn]);

  return { session, begin, redraw, finish, cancel, discard };
}
