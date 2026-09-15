import type { MapTool } from '@/entities/editor';
import type { PlantingZoneAssignment } from '@green/api-client';

export interface ZoneDrawingContext {
  purpose: 'manage' | 'place';
  nextTool?: MapTool;
}

export interface ZoneReviewDraft extends ZoneDrawingContext {
  zone: PlantingZoneAssignment;
}

export interface ZoneDrawingSession extends ZoneDrawingContext {
  target: string;
  /** Review redraw keeps the draft identity and metadata, not its geometry. */
  zone?: PlantingZoneAssignment;
}
