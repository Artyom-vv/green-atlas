import { useEffect, useState } from 'react';
import { assistantWidth } from './assistantContext';
/** Compatibility preference for the standalone surface; dock width belongs to Workbench. */
export function useAssistantLayout() {
  const [viewport, setViewport] = useState(window.innerWidth);
  const [fraction, setFraction] = useState(() => {
    try {
      return Number(localStorage.getItem('green-assistant-width') ?? 0.31);
    } catch {
      return 0.31;
    }
  });
  const width = assistantWidth(viewport, fraction);
  useEffect(() => {
    const measure = () => setViewport(window.innerWidth);
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem('green-assistant-width', String(fraction));
    } catch {
      /* Optional preference. */
    }
  }, [fraction]);

  return {
    width,
    setWidth: (px: number) => setFraction(px / viewport),
    resetWidth: () => setFraction(0.31),
  };
}
