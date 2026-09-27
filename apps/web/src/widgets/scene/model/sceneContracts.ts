import type {
  PlantingZoneAssignment,
  SceneSnapshot,
  ValidationIssue,
} from '@green/api-client';
import type { PlanViewState } from '@/entities/editor/model/planViewState';
export interface SceneHoverPresentation {
  objectId: string;
  x: number;
  y: number;
}
export interface SceneReviewHandle {
  getViewState: () => PlanViewState | undefined;
  fitPlantings: () => void;
  fitSelection: () => void;
  fitExtent: (extent: readonly number[]) => void;
}
export interface SceneReviewOptions {
  snapshot?: SceneSnapshot;
  zones?: PlantingZoneAssignment[];
  selectedZoneIds?: string[];
  horizon: number;
  showGrowthControl?: boolean;
  selectedIds: string[];
  issues?: ValidationIssue[];
  active?: boolean;
  loading?: boolean;
  error?: string;
  onHorizon: (horizon: number) => void;
  onSelect: (objectId: string) => void;
  onMoveTarget?: (coordinate: [number, number]) => void;
  initialViewState?: PlanViewState;
  onViewStateChange?: (state: PlanViewState) => void;
}
