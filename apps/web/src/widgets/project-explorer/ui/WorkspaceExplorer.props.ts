import type { PlanObject, PlantingZoneAssignment } from '@green/api-client';
import type { ReactNode } from 'react';
import type { ExplorerTab } from './ExplorerNavigation';

export interface WorkspaceExplorerProps {
  activeTab?: ExplorerTab;
  onTabChange?: (tab: ExplorerTab) => void;
  zones: PlantingZoneAssignment[];
  objects: PlanObject[];
  selectedIds: string[];
  selectedZoneIds: string[];
  speciesNames: ReadonlyMap<string, string>;
  layers: ReactNode;
  sourceName?: string;
  sourceCount: number;
  disabled?: boolean;
  zoneSelectionDisabled?: boolean;
  onSelect: (ids: string[]) => void;
  onZone: (zone: PlantingZoneAssignment) => void;
  onZonesChange?: (ids: string[]) => void;
  onManageZones: () => void;
  onManagePlantings?: () => void;
  onSource: () => void;
}
