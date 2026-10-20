import { useEffect, useLayoutEffect, useRef } from 'react';
import type { BrushDraft } from './brushForm';

/** Debounce read-only brush previews; writes stay owned by the scenario. */
export function useBrushPreviewSchedule(
  draft: BrushDraft | undefined,
  applying: boolean,
  onPreview: (draft: BrushDraft) => void,
) {
  const preview = useRef(onPreview);
  useLayoutEffect(() => {
    preview.current = onPreview;
  }, [onPreview]);
  useEffect(() => {
    if (!draft || applying) return;
    const timer = setTimeout(() => preview.current(draft), 100);
    return () => clearTimeout(timer);
  }, [draft, applying]);
}
