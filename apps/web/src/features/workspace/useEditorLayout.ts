import { useCallback, useEffect, useState } from 'react';
import { editorLayoutMetrics, type LayoutPreference } from './editorLayoutMetrics';

const storageKey = 'green-editor-layout-v3';
export function useEditorLayout() {
  const [element, setElement] = useState<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => setElement(node), []);
  const [preference, setPreference] = useState<LayoutPreference>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) ?? '{}');
      return Object.fromEntries(Object.entries(saved).filter(([key, value]) => ['width', 'projectHeight', 'resultsHeight'].includes(key) && typeof value === 'number' && Number.isFinite(value) && value > 0 && value < 1));
    } catch { return {}; }
  });
  const [container, setContainer] = useState({ width: window.innerWidth, height: window.innerHeight, rem: 16 });
  useEffect(() => {
    if (!element) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      setContainer({ width: rect.width, height: rect.height, rem: parseFloat(getComputedStyle(document.documentElement).fontSize) || 16 });
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, [element]);
  useEffect(() => {
    const timer = window.setTimeout(() => { try { localStorage.setItem(storageKey, JSON.stringify(preference)); } catch { /* Storage may be unavailable. */ } }, 150);
    return () => window.clearTimeout(timer);
  }, [preference]);
  return {
    ref, ...editorLayoutMetrics(container, preference),
    set: (key: keyof LayoutPreference, value: number) => setPreference(current => ({ ...current, [key]: value / Math.max(1, key === 'width' ? container.width : container.height) })),
    reset: (key: keyof LayoutPreference) => setPreference(current => { const next = { ...current }; delete next[key]; return next; }),
  };
}
