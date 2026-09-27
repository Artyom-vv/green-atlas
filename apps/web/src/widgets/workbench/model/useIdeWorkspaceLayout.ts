import { useStore } from 'zustand';
import { createLayoutStore } from './layoutStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  IDE_WORKSPACE_STORAGE_KEY,
  ideWorkspaceLayout,
  readIdeWorkspacePreference,
  type IdeWorkspacePreference,
} from './ideWorkspaceLayout';

export interface IdeWorkspaceLayoutOptions {
  resourcesOpen?: boolean;
  onResourcesOpenChange?: (open: boolean) => void;
  rightOpen?: boolean;
  onRightOpenChange?: (open: boolean) => void;
  resultsOpen?: boolean;
  onResultsOpenChange?: (open: boolean) => void;
}

export function useIdeWorkspaceLayout(options: IdeWorkspaceLayoutOptions = {}) {
  const [element, setElement] = useState<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => setElement(node), []);
  const [store] = useState(() => {
    let preference: IdeWorkspacePreference = {};
    try {
      preference = readIdeWorkspacePreference(
        JSON.parse(localStorage.getItem(IDE_WORKSPACE_STORAGE_KEY) ?? '{}'),
      );
    } catch {
      /* Preferences are optional. */
    }
    return createLayoutStore(preference);
  });
  const state = useStore(store);
  const { preference, priority } = state;
  const [container, setContainer] = useState({
    width: typeof window === 'undefined' ? 1280 : window.innerWidth,
    height: typeof window === 'undefined' ? 720 : window.innerHeight - 56,
  });

  const resourcesOpen = options.resourcesOpen ?? state.resourcesOpen;
  const previousResourcesOpen = useRef(resourcesOpen);
  const rightOpen = options.rightOpen ?? state.rightOpen;
  const resultsOpen = options.resultsOpen ?? state.resultsOpen;
  const prioritize = state.prioritize;
  const prioritizeRight = useCallback(() => prioritize('right'), [prioritize]);

  useEffect(() => {
    if (rightOpen) prioritizeRight();
  }, [rightOpen, prioritizeRight]);
  useEffect(() => {
    if (resourcesOpen && !previousResourcesOpen.current)
      prioritize('resources');
    previousResourcesOpen.current = resourcesOpen;
  }, [resourcesOpen, prioritize]);

  useEffect(() => {
    if (!element) return;
    const measure = () => {
      const { width, height } = element.getBoundingClientRect();
      if (width > 0 && height > 0)
        setContainer((current) =>
          current.width === width && current.height === height
            ? current
            : { width, height },
        );
    };
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener('resize', measure);
    measure();
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [element]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        localStorage.setItem(
          IDE_WORKSPACE_STORAGE_KEY,
          JSON.stringify(preference),
        );
      } catch {
        /* Sizing remains usable when storage is unavailable. */
      }
    }, 150);
    return () => window.clearTimeout(timer);
  }, [preference]);

  const setRightOpen = (open: boolean) => {
    if (open) prioritize('right');
    if (options.rightOpen === undefined) state.setOpen('right', open);
    options.onRightOpenChange?.(open);
  };
  return {
    ref,
    preference,
    resourcesOpen,
    rightOpen,
    resultsOpen,
    prioritizeRight,
    ...ideWorkspaceLayout(container, preference, {
      resourcesOpen,
      rightOpen,
      resultsOpen,
      priority,
    }),
    setResourcesOpen: (open: boolean) => {
      if (open) prioritize('resources');
      if (options.resourcesOpen === undefined) state.setOpen('resources', open);
      options.onResourcesOpenChange?.(open);
    },
    setRightOpen,
    setResultsOpen: (open: boolean) => {
      if (options.resultsOpen === undefined) state.setOpen('results', open);
      options.onResultsOpenChange?.(open);
    },
    setSize: state.setSize,
    resetSize: state.resetSize,
  };
}
