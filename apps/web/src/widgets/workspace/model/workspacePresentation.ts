import type { MapTool } from '@/entities/editor';
import type { IdeRightTab } from '@/widgets/workbench';
export const ideTabForTool = (
  tool: MapTool,
  placementOwnsContext = false,
): IdeRightTab =>
  placementOwnsContext ||
  ['pattern_row', 'pattern_fill', 'brush', 'add_tree', 'add_shrub'].includes(
    tool,
  )
    ? 'tool'
    : 'inspector';
