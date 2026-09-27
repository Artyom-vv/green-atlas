import { useLayoutEffect, useRef } from 'react';

/** Focus follows committed catalog/details navigation; draft changes stay in RHF. */
export function useSpeciesCatalogFocus(browsing: boolean, selectedId?: string) {
  const contentRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const pending = useRef<'details' | 'search' | null>(null);
  useLayoutEffect(() => {
    if (pending.current === 'details' && !browsing) {
      headingRef.current?.focus();
      pending.current = null;
    } else if (pending.current === 'search' && browsing) {
      contentRef.current
        ?.querySelector<HTMLInputElement>(
          'input[aria-label="Поиск в каталоге пород"]',
        )
        ?.focus();
      pending.current = null;
    }
  }, [browsing, selectedId]);
  return {
    contentRef,
    headingRef,
    prepareDetails: () => {
      pending.current = 'details';
    },
    prepareSearch: () => {
      pending.current = 'search';
    },
  };
}
