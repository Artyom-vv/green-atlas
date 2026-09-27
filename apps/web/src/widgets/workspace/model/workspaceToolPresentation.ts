import type { MapTool } from '@/entities/editor';

export const WORKSPACE_TOOL_NAMES: Record<MapTool, string> = {
  select: 'Выбор',
  pan: 'Перемещение карты',
  select_box: 'Выбор рамкой',
  select_lasso: 'Выбор лассо',
  pattern_fill: 'Разместить посадки',
  pattern_row: 'Посадки вдоль линии',
  brush: 'Кисть посадок',
  add_tree: 'Посадить дерево',
  add_shrub: 'Посадить кустарник',
  move: 'Перемещение посадок',
  copy: 'Копирование посадок',
  draw_area: 'Граница участка',
};

export interface WorkspaceToolHintInput {
  tool: MapTool;
  selectedZoneCount: number;
  rowAcceptedCount?: number;
  rowInputMode: 'pick' | 'draw' | 'ready';
  hasRowAxis: boolean;
}

export function workspaceToolHint({
  tool,
  selectedZoneCount,
  rowAcceptedCount,
  rowInputMode,
  hasRowAxis,
}: WorkspaceToolHintInput): string {
  if (tool === 'brush')
    return selectedZoneCount
      ? 'Проведите по выбранным участкам'
      : 'Выберите участки в проекте';
  if (tool === 'pattern_row') {
    if (rowAcceptedCount !== undefined)
      return rowAcceptedCount
        ? 'Проверенные позиции на карте. Добавьте их или измените условия'
        : 'Позиции не приняты. Посмотрите причины в результате проверки';
    if (rowInputMode === 'draw') return 'Поставьте точки и завершите линию';
    return hasRowAxis
      ? 'Эскиз на карте. Настройте ряд и проверьте места'
      : 'Выберите линию DXF или нарисуйте свою';
  }
  if (tool === 'pattern_fill')
    return selectedZoneCount
      ? `Участков для расчёта: ${selectedZoneCount}`
      : 'Выберите участки в проекте';
  if (tool === 'draw_area') return 'Поставьте точки и замкните контур';
  if (tool === 'move' || tool === 'copy') return 'Укажите новое место на карте';
  if (tool === 'add_tree' || tool === 'add_shrub')
    return 'Выберите допустимое место на карте';
  return '';
}
