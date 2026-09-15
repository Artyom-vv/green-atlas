import type { MapTool } from '@/entities/editor';
import {
  BoxSelect,
  Grid3x3,
  Hand,
  LassoSelect,
  MousePointer2,
  Paintbrush,
  Route,
  Shrub,
  TreeDeciduous,
  type LucideIcon,
} from 'lucide-react';

export interface ToolbarItem {
  tool: MapTool;
  icon: LucideIcon;
  label: string;
  requires2D?: boolean;
  editsPlan?: boolean;
}

export const toolbarGroups = [
  [
    {
      tool: 'select',
      icon: MousePointer2,
      label: 'Выбрать. Shift — добавить к выбору',
    },
    {
      tool: 'select_box',
      icon: BoxSelect,
      label: 'Выбрать рамкой',
      requires2D: true,
    },
    {
      tool: 'select_lasso',
      icon: LassoSelect,
      label: 'Выбрать лассо',
      requires2D: true,
    },
    { tool: 'pan', icon: Hand, label: 'Перемещать карту' },
  ],
  [
    {
      tool: 'pattern_fill',
      icon: Grid3x3,
      label: 'Разместить посадки',
      requires2D: true,
      editsPlan: true,
    },
    {
      tool: 'brush',
      icon: Paintbrush,
      label: 'Кисть посадок',
      requires2D: true,
      editsPlan: true,
    },
    {
      tool: 'pattern_row',
      icon: Route,
      label: 'Посадки вдоль линии',
      requires2D: true,
      editsPlan: true,
    },
  ],
  [
    {
      tool: 'add_tree',
      icon: TreeDeciduous,
      label: 'Посадить дерево',
      requires2D: true,
      editsPlan: true,
    },
    {
      tool: 'add_shrub',
      icon: Shrub,
      label: 'Посадить кустарник',
      requires2D: true,
      editsPlan: true,
    },
  ],
] satisfies ToolbarItem[][];
