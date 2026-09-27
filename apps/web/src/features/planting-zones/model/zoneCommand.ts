import type { PlantingZoneAssignment, Project } from '@green/api-client';
import type { MapTool } from '@/entities/editor';

export interface PlacementZoneSave {
  zone: PlantingZoneAssignment;
  nextTool?: MapTool;
}
export interface ManagedZonesSave {
  zones: PlantingZoneAssignment[];
  focusId?: string;
}
export type ZoneSaveCommand =
  | (PlacementZoneSave & { kind: 'placement' })
  | (ManagedZonesSave & { kind: 'managed' });

export type ZoneCommandContext = {
  projectId: string;
  expectedStateVersion: Project['state_version'];
  zones: PlantingZoneAssignment[];
} & (
  | (PlacementZoneSave & { kind: 'placement' })
  | { kind: 'managed'; focusId?: string }
);

/** A full-list PUT must keep the exact zones and revision accepted by its caller. */
export function captureZoneCommand(
  projectId: string,
  project: Project,
  command: ZoneSaveCommand,
): ZoneCommandContext {
  const captured = structuredClone(command);
  const basis = {
    projectId,
    expectedStateVersion: project.state_version,
  };
  return captured.kind === 'placement'
    ? {
        ...basis,
        ...captured,
        zones: [
          ...structuredClone(project.planting_zones ?? []),
          captured.zone,
        ],
      }
    : { ...basis, ...captured };
}

export const UNKNOWN_ZONE_SAVE_NOTICE =
  'Состояние проекта обновлено. Проверьте участки перед повторным сохранением';
