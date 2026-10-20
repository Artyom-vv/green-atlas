import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

/** A single operation owns the header caption, clock and loading strip. */
export function ViewportLoading({
  label,
  headerSlot,
  progressSlot,
  startedAt,
}: {
  label: string;
  headerSlot?: HTMLElement | null;
  progressSlot?: HTMLElement | null;
  startedAt?: number;
}) {
  const [elapsed, setElapsed] = useState(() =>
    startedAt !== undefined
      ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
      : 0,
  );
  useEffect(() => {
    const started = startedAt ?? Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [startedAt]);
  const caption = (
    <span
      role="status"
      aria-label={label}
      className="order-2 flex max-w-56 min-w-0 items-center gap-2 text-xs text-neutral-600"
    >
      <span className="truncate">{label}</span>
      <span aria-live="off" className="shrink-0 tabular-nums">
        {elapsed} с
      </span>
    </span>
  );
  return (
    <>
      {headerSlot && createPortal(caption, headerSlot)}
      {progressSlot &&
        createPortal(<span data-slot="scene-pending" hidden />, progressSlot)}
    </>
  );
}
