import type { MapTool } from '../../domain-ui/MapToolbar';

export type InspectorView = 'new-zones' | 'zones' | 'layer' | 'source' | 'species' | 'recommendation' | 'recommendation-setup' | 'pattern' | 'brush' | 'changes' | 'object' | 'group' | 'area' | 'overview';
type InspectorContext = {
  panel: string | null; hasPlan: boolean; sourcePreview: boolean; hasLayer: boolean;
  tool: MapTool; drawingPlacementArea: boolean; hasPattern: boolean;
  hasChange: boolean; hasRecommendation: boolean; assigningSpecies: boolean;
  selectionCount: number; hasArea: boolean;
  recommendationOpen?: boolean;
};

/** A single foreground inspector. Reading another module may hide a live
 * tool form, but never creates two independent primary panels on screen. */
export function resolveInspectorView(context: InspectorContext): InspectorView {
  if (context.hasLayer) return 'layer';
  if (context.sourcePreview) return 'source';
  if (context.panel === 'zones' && !context.hasPlan) return 'new-zones';
  if (context.selectionCount === 1) return 'object';
  if (context.selectionCount > 1) return 'group';
  if (context.hasArea) return 'area';
  return 'overview';
}
