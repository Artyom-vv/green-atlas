import { type MapFocusResult } from '@/entities/editor/model/mapFocus';
import { type PlanViewState } from '@/entities/editor/model/planViewState';
import type { PlantingZoneAssignment } from '@green/api-client';

export type { NumericExtent as MapExtent } from '@/shared/geometry/mapExtent';

export type MapAreaTarget = {
  sourceId: string;
  plantingZoneId?: string;
  geometry?: { type: 'Polygon'; coordinates: number[][][] };
  kind: string;
  label: string;
  detail: string;
  selectable: boolean;
};

export type MapPreviewTarget = {
  objectId: string;
  status: 'allowed' | 'blocked' | 'soft_conflict' | 'unknown';
  code: string;
  reason: string;
  suggestedAction?: string;
};

export type MapHoverItem = {
  id: string;
  kind: string;
  label: string;
  detail: string;
  target?: MapAreaTarget;
  preview?: MapPreviewTarget;
};

export type MapHoverTarget = {
  kind: 'area' | 'preview';
  items: MapHoverItem[];
  pixel: [number, number];
};

export type MapViewportHandle = {
  abortDrawing: () => void;
  finishRowDrawing: () => void;
  abortRowDrawing: () => void;
  fit: () => void;
  fitGeometry: (geometry: PlantingZoneAssignment['geometry']) => void;
  focusGeometry: (
    geometry: PlantingZoneAssignment['geometry'],
    revision: number,
    signal: AbortSignal,
  ) => Promise<MapFocusResult>;
  fitLayer: (sourceLayer: string, bounds?: readonly number[] | null) => void;
  fitSelection: (id: string) => void;
  fitObjects: (ids: string[]) => void;
  fitPlan: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  getViewState: () => PlanViewState | undefined;
  applyViewState: (state: PlanViewState) => void;
};

export type PlacementPreview = {
  coordinate: [number, number];
  radius: number;
  status: 'allowed' | 'blocked' | 'unknown';
};

export type BrushDrawMode = 'replace' | 'append' | 'subtract';
