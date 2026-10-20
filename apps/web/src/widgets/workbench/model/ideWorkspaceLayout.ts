/** Рабочее пространство распределяет место между ресурсами, картой и инспектором. */
import { RESULTS_STRIP_HEIGHT } from '@/shared/ui/results/resultsLayout';

export type IdeWorkspaceSide = 'resources' | 'right';
export type IdeWorkspaceSize =
  'resourcesWidth' | 'rightWidth' | 'resultsHeight';
export type IdeWorkspacePreference = Partial<Record<IdeWorkspaceSize, number>>;

export const IDE_WORKSPACE_STORAGE_KEY = 'green-ide-workspace-layout-v1';
export const IDE_WORKSPACE_DEFAULTS = {
  resourcesWidth: 260,
  rightWidth: 300,
  resultsHeight: 220,
};
export const IDE_WORKSPACE_RAIL = 48;
export const IDE_WORKSPACE_MIN_MAP = 640;
export const IDE_WORKSPACE_RESULTS_TABS = RESULTS_STRIP_HEIGHT;

const limits = {
  resourcesWidth: [180, 320],
  rightWidth: [300, 480],
  resultsHeight: [160, 600],
} as const;
const bound = (value: number, min: number, max: number) =>
  Math.round(Math.max(min, Math.min(max, value)));

export function readIdeWorkspacePreference(
  value: unknown,
): IdeWorkspacePreference {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const result: IdeWorkspacePreference = {};
  for (const key of Object.keys(limits) as IdeWorkspaceSize[]) {
    const candidate = (value as Record<string, unknown>)[key];
    if (typeof candidate === 'number' && Number.isFinite(candidate)) {
      const [min, max] = limits[key];
      result[key] = bound(candidate, min, max);
    }
  }
  return result;
}

/** Ограничения окна временно сжимают панели, сохраняя выбранные пользователем размеры. */
export function ideWorkspaceLayout(
  container: { width: number; height: number },
  preference: IdeWorkspacePreference = {},
  visibility = {
    resourcesOpen: true,
    rightOpen: true,
    resultsOpen: false,
    priority: 'right' as IdeWorkspaceSide,
  },
) {
  const width = Math.max(0, container.width);
  const height = Math.max(0, container.height);
  const wanted = {
    ...IDE_WORKSPACE_DEFAULTS,
    ...readIdeWorkspacePreference(preference),
  };
  let resourcesExpanded = visibility.resourcesOpen;
  let rightExpanded = visibility.rightOpen;

  // Когда места для двух панелей и карты недостаточно, оставляем панель,
  // которую пользователь открыл последней; другая сворачивается в полоску.
  if (
    resourcesExpanded &&
    rightExpanded &&
    width <
      IDE_WORKSPACE_MIN_MAP + limits.resourcesWidth[0] + limits.rightWidth[0]
  ) {
    if (visibility.priority === 'resources') rightExpanded = false;
    else resourcesExpanded = false;
  }
  let resourcesWidth = resourcesExpanded
    ? wanted.resourcesWidth
    : IDE_WORKSPACE_RAIL;
  let rightWidth = rightExpanded ? wanted.rightWidth : IDE_WORKSPACE_RAIL;
  const budget = Math.max(0, width - IDE_WORKSPACE_MIN_MAP);
  const shrinkResources = () => {
    if (resourcesExpanded)
      resourcesWidth = Math.max(
        limits.resourcesWidth[0],
        Math.min(resourcesWidth, budget - rightWidth),
      );
  };
  const shrinkRight = () => {
    if (rightExpanded)
      rightWidth = Math.max(
        limits.rightWidth[0],
        Math.min(rightWidth, budget - resourcesWidth),
      );
  };
  if (visibility.priority === 'resources') {
    shrinkRight();
    shrinkResources();
  } else {
    shrinkResources();
    shrinkRight();
  }

  // В очень узком окне сохраняем доступные контролы и не допускаем
  // отрицательной ширины колонок, даже когда карта уже не помещается целиком.
  resourcesWidth = Math.min(resourcesWidth, Math.max(0, width - rightWidth));
  rightWidth = Math.min(rightWidth, Math.max(0, width - resourcesWidth));
  const mapWidth = Math.max(0, width - resourcesWidth - rightWidth);
  const maxResults = Math.round(
    Math.max(
      0,
      Math.min(height * 0.45, height - 260 - IDE_WORKSPACE_RESULTS_TABS),
    ),
  );
  const minResults = Math.min(limits.resultsHeight[0], maxResults);
  const resultsHeight = bound(wanted.resultsHeight, minResults, maxResults);

  return {
    resourcesExpanded,
    rightExpanded,
    resourcesWidth,
    rightWidth,
    resultsHeight,
    mapWidth,
    resourcesAutoCollapsed: visibility.resourcesOpen && !resourcesExpanded,
    rightAutoCollapsed: visibility.rightOpen && !rightExpanded,
    mapMinimumSatisfied: mapWidth >= IDE_WORKSPACE_MIN_MAP,
    resultsTotalHeight:
      IDE_WORKSPACE_RESULTS_TABS + (visibility.resultsOpen ? resultsHeight : 0),
    resourcesMin: Math.min(limits.resourcesWidth[0], resourcesWidth),
    resourcesMax: Math.max(
      Math.min(limits.resourcesWidth[0], resourcesWidth),
      Math.min(
        limits.resourcesWidth[1],
        width - IDE_WORKSPACE_MIN_MAP - rightWidth,
      ),
    ),
    rightMin: Math.min(limits.rightWidth[0], rightWidth),
    rightMax: Math.max(
      Math.min(limits.rightWidth[0], rightWidth),
      Math.min(
        limits.rightWidth[1],
        width - IDE_WORKSPACE_MIN_MAP - resourcesWidth,
      ),
    ),
    minResults,
    maxResults,
  };
}
