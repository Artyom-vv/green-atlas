// Ограничение ожидания одного ответа, а не всего расчёта. Партия возвращает
// промежуточный результат; отдельному CAD-запросу нужен запас на завершение.
export const PATTERN_PASS_RESPONSE_TIMEOUT_MS = 180_000;

export function patternPreviewPass<Preview>(
  next: (signal: AbortSignal) => Promise<Preview>,
  signal: AbortSignal,
  timeoutMs: number,
): Promise<Preview> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const stop = (reason: unknown) => {
      controller.abort(reason);
      reject(reason);
    };
    const abort = () => stop(signal.reason);
    const timer = setTimeout(
      () =>
        stop(
          new Error('Ответ по текущей партии не получен — повторите расчёт'),
        ),
      timeoutMs,
    );
    const cleanup = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
    };
    signal.addEventListener('abort', abort, { once: true });
    // Поздний ответ отменённого транспорта не публикует прогресс и не запускает
    // следующую партию; оба пути отказа остаются обработанными.
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
