export const MAP_TOOLS = [
  'select',
  'pan',
  'select_box',
  'select_lasso',
  'add_tree',
  'add_shrub',
  'pattern_row',
  'pattern_fill',
  'brush',
  'move',
  'copy',
  'draw_area',
] as const;

export type MapTool = (typeof MAP_TOOLS)[number];

export const SELECTION_MODES = [
  'replace',
  'add',
  'subtract',
  'toggle',
] as const;
export type SelectionMode = (typeof SELECTION_MODES)[number];

export const EDITOR_PANELS = [
  'zones',
  'plantings',
  'issues',
  'history',
  'export',
] as const;
export type EditorPanel = (typeof EDITOR_PANELS)[number] | null;

export const EDITOR_RIGHT_TABS = ['inspector', 'tool', 'assistant'] as const;
export type EditorRightTab = (typeof EDITOR_RIGHT_TABS)[number];

export const EDITOR_RESULTS_TABS = ['issues', 'schedule', 'history'] as const;
export type EditorResultsTab = (typeof EDITOR_RESULTS_TABS)[number];

export type StateUpdate<Value> = Value | ((current: Value) => Value);

export interface EditorViewState {
  panel: EditorPanel;
  resourcesTab: 'project' | 'layers';
  ideRightTab: EditorRightTab;
  leftOpen: boolean;
  rightOpen: boolean;
  resultsOpen: boolean;
  resultsTab: EditorResultsTab;
  activeLayerId: string | undefined;
  visibility: Record<string, boolean>;
}

export const EDITOR_VIEW_DEFAULTS = {
  panel: null,
  resourcesTab: 'project',
  ideRightTab: 'inspector',
  leftOpen: true,
  rightOpen: true,
  resultsOpen: false,
  resultsTab: 'issues',
  activeLayerId: undefined,
} satisfies Omit<EditorViewState, 'visibility'>;

export interface EditorSessionState extends EditorViewState {
  projectId: string;
  tool: MapTool;
  selectedIds: string[];
}
