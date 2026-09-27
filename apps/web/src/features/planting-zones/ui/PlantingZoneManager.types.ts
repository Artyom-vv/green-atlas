import type { PlantingZoneAssignment } from '@green/api-client';
export type ZoneFilter = 'all' | 'selected' | 'empty';
export interface PlantingZoneManagerProps {
  zones: PlantingZoneAssignment[];
  activeId?: string;
  activeIds?: string[];
  onSelectionChange?: (ids: string[]) => void;
  zoneUsage?: Record<string, number>;
  drawing?: boolean;
  saving?: boolean;
  error?: string;
  onFocus: (zone: PlantingZoneAssignment) => void;
  onRename: (zone: PlantingZoneAssignment, label: string) => void;
  onRedraw: (zone: PlantingZoneAssignment) => void;
  onDelete: (zone: PlantingZoneAssignment) => void;
  onDraw: () => void;
  onCancelDraw: () => void;
}
