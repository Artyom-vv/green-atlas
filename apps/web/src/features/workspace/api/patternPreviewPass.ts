import type { PatternPreview } from '@green/api-client';

// Connection watchdog, not a budget for the whole calculation. A normal pass
// yields after 5 seconds between native batches; an individual CAD call has a
// 90-second server timeout. Allow time for that call and the final placement step.
export const PATTERN_PASS_RESPONSE_TIMEOUT_MS = 180_000;

export function patternPreviewPass(
  next: (signal: AbortSignal) => Promise<PatternPreview>,
  signal: AbortSignal,
  timeoutMs: number,
): Promise<PatternPreview> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const stop = (reason: unknown) => {
      controller.abort(reason);
      reject(reason);
    };
    const abort = () => stop(signal.reason);
    const timer = setTimeout(
      () => stop(new Error('Ответ по текущей партии не получен — повторите расчёт')),
      timeoutMs,
    );
    const cleanup = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
    };
    signal.addEventListener('abort', abort, { once: true });
    // Both rejection paths remain observed even if a transport ignores abort.
    // A late response cannot publish progress or start another pass.
    void Promise.resolve()
      .then(() => {
        controller.signal.throwIfAborted();
        return next(controller.signal);
      })
      .then(resolve, reject)
      .finally(cleanup);
    controller.signal.addEventListener('abort', cleanup, { once: true });
  });
}
