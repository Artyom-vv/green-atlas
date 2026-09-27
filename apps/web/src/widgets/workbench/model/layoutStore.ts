import { createStore } from 'zustand/vanilla';
import {
  readIdeWorkspacePreference,
  type IdeWorkspacePreference,
  type IdeWorkspaceSide,
  type IdeWorkspaceSize,
} from './ideWorkspaceLayout';

interface LayoutState {
  preference: IdeWorkspacePreference;
  resourcesOpen: boolean;
  rightOpen: boolean;
  resultsOpen: boolean;
  priority: IdeWorkspaceSide;
}

interface LayoutActions {
  setOpen: (area: 'resources' | 'right' | 'results', open: boolean) => void;
  prioritize: (side: IdeWorkspaceSide) => void;
  setSize: (key: IdeWorkspaceSize, value: number) => void;
  resetSize: (key: IdeWorkspaceSize) => void;
}

export function createLayoutStore(preference: IdeWorkspacePreference) {
  return createStore<LayoutState & LayoutActions>()((set) => ({
    preference: readIdeWorkspacePreference(preference),
    resourcesOpen: true,
    rightOpen: true,
    resultsOpen: false,
    priority: 'right',
    setOpen: (area, open) => set({ [`${area}Open`]: open }),
    prioritize: (priority) => set({ priority }),
    setSize: (key, value) => {
      if (!Number.isFinite(value)) return;
      set(({ preference: current }) => ({
        preference: readIdeWorkspacePreference({ ...current, [key]: value }),
      }));
    },
    resetSize: (key) =>
      set(({ preference: current }) => {
        const preference = { ...current };
        delete preference[key];
        return { preference };
      }),
  }));
}
