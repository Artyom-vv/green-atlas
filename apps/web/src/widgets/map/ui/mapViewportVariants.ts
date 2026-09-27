import type { MapTool } from '@/entities/editor';
import { tv } from '@green/ui';

const toolCursors = {
  select: 'active:cursor-grabbing',
  pan: 'cursor-grab active:cursor-grabbing',
  select_box: 'cursor-crosshair',
  select_lasso: 'cursor-crosshair',
  add_tree: 'cursor-crosshair',
  add_shrub: 'cursor-crosshair',
  pattern_row: 'cursor-crosshair',
  pattern_fill: 'cursor-crosshair',
  brush: 'cursor-crosshair',
  move: 'cursor-move',
  copy: 'cursor-move',
  draw_area: 'cursor-crosshair',
} satisfies Record<MapTool, string>;

export const mapViewport = tv({
  base: 'absolute inset-0',
  variants: { tool: toolCursors },
});
