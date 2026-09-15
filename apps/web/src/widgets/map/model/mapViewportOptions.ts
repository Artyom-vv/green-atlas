import type { MapTool, SelectionMode } from '@/entities/editor';
import { type LiveBrushSettings } from '@/entities/planting';
import type {
  RowAxis,
  RowSketchSettings,
} from '@/entities/planting/model/rowSketch';
import type { MoveLiveValidation } from '@/features/plan-changes/model/moveLiveValidation';
import type {
  CadAppearanceMode,
  CadRenderState,
  MapCadSource,
} from './cadSource';
import {
  type BrushDrawMode,
  type MapAreaTarget,
  type MapExtent,
  type MapHoverTarget,
  type PlacementPreview,
} from '@/widgets/map/model/mapContracts';
import type {
  BrushStroke,
  ChangeSetPreview,
  PlanChangeSetDraft,
  PlanObject,
  PlantingZoneAssignment,
  ZoneChangePreview,
} from '@green/api-client';

export interface MapViewportOptions {
  rowResultReady?: boolean;
  rowAxis?: RowAxis;
  rowSettings?: RowSketchSettings;
  rowInputMode?: 'pick' | 'draw' | 'ready';
  onRowDrawingPoints?: (count: number) => void;
  metadataOnlyIds?: readonly string[];
  editPending?: boolean;
  interactionDisabled?: boolean;
  renderMode?: CadAppearanceMode;
  cadSource?: MapCadSource;
  onCadRenderState?: (state: CadRenderState) => void;
  geometry?: Record<string, unknown>;
  geometryRevision?: number;
  initialExtent?: MapExtent;
  objects: PlanObject[];
  growthHorizon?: number;
  draftPlantingZones?: PlantingZoneAssignment[];
  hiddenLayerNames?: string[];
  selectedIds?: string[];
  highlightedPlantingZoneId?: string;
  highlightedPlantingZoneIds?: string[];
  focusGeometry?: Record<string, unknown>;
  placementPreview?: PlacementPreview;
  changePreview?: ChangeSetPreview;
  zoneChangePreview?: ZoneChangePreview;
  changeDraft?: PlanChangeSetDraft;
  liveMoveValidation?: MoveLiveValidation;
  tool: MapTool;
  brushStrokes?: readonly BrushStroke[];
  brushSettings?: LiveBrushSettings;
  brushZones?: PlantingZoneAssignment[];
  onBrushGesture?: (active: boolean) => void;
  brushEnabled?: boolean;
  brushWidthM?: number;
  brushOperation?: 'add' | 'subtract';
  onSelect: (id?: string, mode?: SelectionMode) => void;
  onSelectMany?: (ids: string[], mode: SelectionMode) => void;
  onCoordinate: (coordinate: [number, number]) => void;
  onDrawArea?: (geometry: {
    type: 'Polygon';
    coordinates: number[][][];
  }) => void;
  onDrawAxis?: (
    geometry: { type: 'LineString'; coordinates: number[][] },
    source?: { type: 'dxf' | 'manual'; label: string },
  ) => void;
  onDrawBrush?: (stroke: BrushStroke, mode: BrushDrawMode) => void;
  onMapArea?: (target: MapAreaTarget, mode: SelectionMode) => void;
  onPointerCoordinate?: (coordinate?: [number, number]) => void;
  onMoveCoordinate?: (coordinate?: [number, number]) => void;
  onMapHover?: (target?: MapHoverTarget) => void;
  onMapInspect?: (target?: MapHoverTarget) => void;
  onExtentChange?: (extent: MapExtent, resolution: number) => void;
  onSelectionAnchor?: (pixel?: [number, number]) => void;
  onTranslateSelectionEnd?: (coordinate: [number, number]) => void;
}
