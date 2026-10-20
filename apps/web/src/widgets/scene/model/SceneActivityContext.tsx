import { createContext, useContext, useState, type ReactNode } from 'react';

const Context = createContext<{
  headerSlot: HTMLElement | null;
  setHeaderSlot: (node: HTMLElement | null) => void;
  progressSlot: HTMLElement | null;
  setProgressSlot: (node: HTMLElement | null) => void;
} | null>(null);

/** Layout slots only. Operation state stays with the scene, never mirrored. */
export function SceneActivityProvider({ children }: { children: ReactNode }) {
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);
  const [progressSlot, setProgressSlot] = useState<HTMLElement | null>(null);
  return (
    <Context
      value={{ headerSlot, setHeaderSlot, progressSlot, setProgressSlot }}
    >
      {children}
    </Context>
  );
}
export const useSceneActivity = () => useContext(Context);
