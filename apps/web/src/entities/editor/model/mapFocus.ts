export type MapFocusResult = {
  status: 'completed' | 'failed' | 'cancelled';
  error_code?:
    'MAP_NOT_READY' | 'MAP_UNAVAILABLE' | 'CONTROL_STALE' | 'FOCUS_INTERRUPTED';
};
export type MapFocusSnapshot = {
  ready: boolean;
  revision?: number;
  fit: (complete: (completed: boolean) => void) => () => void;
};

/** A camera operation completes only through the map's own animation callback. */
export function awaitMapGeometryFit(
  snapshot: () => MapFocusSnapshot,
  revision: number,
  signal: AbortSignal,
  timeoutMs = 10_000,
): Promise<MapFocusResult> {
  return new Promise((resolve) => {
    let settled = false;
    let started = false;
    let frame = 0;
    let cancelFit: (() => void) | undefined;
    const finish = (result: MapFocusResult) => {
      if (settled) return;
      settled = true;
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      resolve(result);
    };
    const abort = () => {
      finish({ status: 'cancelled', error_code: 'FOCUS_INTERRUPTED' });
      cancelFit?.();
    };
    const timer = setTimeout(() => {
      finish({
        status: 'failed',
        error_code: started ? 'FOCUS_INTERRUPTED' : 'MAP_NOT_READY',
      });
      cancelFit?.();
    }, timeoutMs);
    const check = () => {
      if (settled) return;
      if (signal.aborted) {
        abort();
        return;
      }
      const current = snapshot();
      if (current.revision !== undefined && current.revision > revision) {
        finish({ status: 'failed', error_code: 'CONTROL_STALE' });
        cancelFit?.();
        return;
      }
      if (!started && current.ready && current.revision === revision) {
        started = true;
        cancelFit = current.fit((completed) => {
          const latest = snapshot();
          finish(
            completed &&
              !signal.aborted &&
              latest.ready &&
              latest.revision === revision
              ? { status: 'completed' }
              : {
                  status: 'failed',
                  error_code:
                    latest.revision !== revision
                      ? 'CONTROL_STALE'
                      : 'FOCUS_INTERRUPTED',
                },
          );
        });
      }
      if (!settled) frame = requestAnimationFrame(check);
    };
    signal.addEventListener('abort', abort, { once: true });
    // Let map initialization, initial framing and geometry ingestion settle first.
    frame = requestAnimationFrame(check);
  });
}
