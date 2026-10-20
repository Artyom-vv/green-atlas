import { useEffect, useState } from 'react';
import type { LayerMapping } from '@green/api-client';
import { mappingKey } from './preparationRecovery';

type Mappings = Record<string, LayerMapping>;
interface History {
  source: string;
  savedKey: string;
  snapshots: Mappings[];
}

/** Undo belongs to the local draft; source replacement or a confirmed save ends it. */
export function useMappingUndo({
  source,
  mappings,
  savedMappings,
  onChange,
}: {
  source: string;
  mappings: Mappings;
  savedMappings: Mappings;
  onChange: (next: Mappings) => void;
}) {
  const [history, setHistory] = useState<History>();
  const savedKey = mappingKey(Object.values(savedMappings));
  useEffect(() => {
    setHistory((current) =>
      current?.source === source && current.savedKey === savedKey
        ? current
        : undefined,
    );
  }, [source, savedKey]);
  const snapshots =
    history?.source === source && history.savedKey === savedKey
      ? history.snapshots
      : [];
  const change = (next: Mappings) => {
    if (mappingKey(Object.values(next)) === mappingKey(Object.values(mappings)))
      return;
    setHistory({
      source,
      savedKey,
      snapshots: [...snapshots.slice(-39), structuredClone(mappings)],
    });
    onChange(next);
  };
  const undo = () => {
    const previous = snapshots.at(-1);
    if (!previous) return;
    setHistory({ source, savedKey, snapshots: snapshots.slice(0, -1) });
    onChange(structuredClone(previous));
  };
  return { change, undo, canUndo: snapshots.length > 0 };
}
