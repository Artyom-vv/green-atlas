import { useRef, type RefObject } from 'react';

/** Lazy initialization for an instance owned by one mounted adapter. The caller
 * owns disposal; the factory must not attach listeners or touch external DOM. */
export function useOwnedRef<T>(create: () => T): RefObject<T> {
  const slot = useRef<RefObject<T> | null>(null);
  if (slot.current === null) slot.current = { current: create() };
  return slot.current;
}
