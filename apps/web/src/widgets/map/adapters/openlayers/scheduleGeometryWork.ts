interface GeometryTaskScheduler {
  postTask(
    callback: () => void,
    options: { priority: 'user-visible'; signal: AbortSignal },
  ): Promise<void>;
}

/** Yield computation to the event loop without waiting for the next paint. */
export function scheduleGeometryWork(callback: () => void): () => void {
  let cancelled = false;
  const run = () => {
    if (!cancelled) callback();
  };
  const scheduler = (
    globalThis as typeof globalThis & { scheduler?: GeometryTaskScheduler }
  ).scheduler;
  if (scheduler?.postTask) {
    const controller = new AbortController();
    void scheduler
      .postTask(run, { priority: 'user-visible', signal: controller.signal })
      .catch((error: unknown) => {
        if (controller.signal.aborted && error === controller.signal.reason)
          return;
        // Preserve unexpected task errors; cancellation alone is consumed.
        setTimeout(() => {
          throw error;
        }, 0);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }
  if (typeof MessageChannel !== 'undefined') {
    const channel = new MessageChannel();
    const close = () => {
      channel.port1.onmessage = null;
      channel.port1.close();
      channel.port2.close();
    };
    channel.port1.onmessage = () => {
      close();
      run();
    };
    channel.port2.postMessage(null);
    return () => {
      cancelled = true;
      close();
    };
  }
  const timer = setTimeout(run, 0);
  return () => {
    cancelled = true;
    clearTimeout(timer);
  };
}
