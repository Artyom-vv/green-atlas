import type { PlantingZoneAssignment } from '@green/api-client';

/**
 * A working area is a spatial selection, not a hidden planting programme.
 * Counts and objectives deliberately stay out of the browser state: the
 * operator decides what to place only after opening the editor.
 */
export function assignmentFromGeometry(
  geometry: PlantingZoneAssignment['geometry'],
  ordinal: number,
  label = `Ручной участок ${ordinal}`,
  id = `manual-zone-${Date.now()}-${ordinal}`,
): PlantingZoneAssignment {
  return {
    id,
    label,
    geometry,
  };
}
