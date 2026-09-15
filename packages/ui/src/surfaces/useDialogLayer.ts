import { useEffect, useRef } from 'react';

const layersByDocument = new WeakMap<Document, symbol[]>();

/**
 * Existing callers compose sibling modal tasks. Base UI coordinates nested
 * roots; this registration only arbitrates sibling dismissal, without owning
 * keyboard listeners, focus, DOM portals or task state.
 */
export function useDialogLayer(open: boolean) {
  const token = useRef(Symbol('dialog-layer'));
  useEffect(() => {
    if (!open) return;
    const layers = layersByDocument.get(document) ?? [];
    layersByDocument.set(document, layers);
    const currentToken = token.current;
    layers.push(currentToken);
    return () => {
      const index = layers.indexOf(currentToken);
      if (index !== -1) layers.splice(index, 1);
    };
  }, [open]);

  return () => layersByDocument.get(document)?.at(-1) === token.current;
}
