import type { MapTool } from '@/entities/editor';

export type ManualWorkspaceInput = {
  tool: MapTool;
  placementPanelOpen: boolean;
  hasPlan: boolean;
  hasPreview: boolean;
  hasPendingZone: boolean;
  initialZoneDrafts: number;
  brushStrokes: number;
  brushDrawing: boolean;
  hasRowAxis: boolean;
  rowDrawingPoints: number;
  placementAreaDrawing: boolean;
  zoneDrawing: boolean;
  mutationPending: boolean;
  brushPreviewPending: boolean;
  createPlanPending: boolean;
  releasePending: boolean;
  /** A committed write still owns navigation until its current project is read. */
  recoveryPending?: boolean;
};

/** Only manual work owns these guards; assistant results must remain reopenable. */
export function manualWorkspaceWork(input: ManualWorkspaceInput) {
  const hasDraft =
    input.hasPreview ||
    input.hasPendingZone ||
    input.brushStrokes > 0 ||
    input.brushDrawing ||
    input.hasRowAxis ||
    input.rowDrawingPoints > 0 ||
    input.placementAreaDrawing ||
    input.zoneDrawing ||
    (!input.hasPlan &&
      (input.initialZoneDrafts > 0 || input.tool === 'draw_area'));
  const pending =
    input.mutationPending ||
    input.brushPreviewPending ||
    input.createPlanPending ||
    input.releasePending ||
    Boolean(input.recoveryPending);
  return {
    hasDraft,
    pending,
    blocksLeaving: hasDraft || pending,
    blocksAssistant:
      hasDraft ||
      pending ||
      (input.tool === 'pattern_fill' && input.placementPanelOpen),
  };
}
