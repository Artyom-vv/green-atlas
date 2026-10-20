import { describe, expect, it } from 'vitest';
import {
  manualWorkspaceWork,
  type ManualWorkspaceInput,
} from './manualWorkspaceWork';

const idle: ManualWorkspaceInput = {
  tool: 'select',
  hasPlan: true,
  hasPreview: false,
  hasPendingZone: false,
  initialZoneDrafts: 0,
  brushStrokes: 0,
  brushDrawing: false,
  hasRowAxis: false,
  rowDrawingPoints: 0,
  placementAreaDrawing: false,
  zoneDrawing: false,
  mutationPending: false,
  brushPreviewPending: false,
  createPlanPending: false,
  releasePending: false,
};

describe('manual workspace work ownership', () => {
  it('protects an unfinished row before an axis exists and releases after the drawing is cancelled', () => {
    const sketch = {
      ...idle,
      tool: 'pattern_row' as const,
      rowDrawingPoints: 1,
      hasRowAxis: false,
    };
    expect(manualWorkspaceWork(sketch)).toMatchObject({
      hasDraft: true,
      blocksLeaving: true,
    });
    expect(
      manualWorkspaceWork({ ...sketch, rowDrawingPoints: 0 }),
    ).toMatchObject({
      hasDraft: false,
      blocksLeaving: false,
    });
  });

  it('protects a live brush gesture before any completed strokes exist', () => {
    const live = {
      ...idle,
      tool: 'brush' as const,
      brushDrawing: true,
      brushStrokes: 0,
    };
    expect(manualWorkspaceWork(live)).toMatchObject({
      hasDraft: true,
      blocksLeaving: true,
    });
    expect(
      manualWorkspaceWork({ ...live, brushDrawing: false, brushStrokes: 1 })
        .blocksLeaving,
    ).toBe(true);
    expect(
      manualWorkspaceWork({ ...live, brushDrawing: false }).blocksLeaving,
    ).toBe(false);
  });

  it.each([
    'mutationPending',
    'brushPreviewPending',
    'createPlanPending',
    'releasePending',
    'recoveryPending',
  ] as const)(
    'protects a pending %s operation even without a draft or active tool',
    (pending) => {
      const active = { ...idle, [pending]: true };
      expect(manualWorkspaceWork(active)).toEqual({
        hasDraft: false,
        pending: true,
        blocksLeaving: true,
      });
      expect(manualWorkspaceWork({ ...active, [pending]: false })).toEqual({
        hasDraft: false,
        pending: false,
        blocksLeaving: false,
      });
    },
  );

  it('retains protection across drawing, zone review, saving and return to placement', () => {
    const phases: ManualWorkspaceInput[] = [
      { ...idle, tool: 'draw_area', placementAreaDrawing: true },
      { ...idle, hasPendingZone: true },
      { ...idle, hasPendingZone: true, mutationPending: true },
      { ...idle, tool: 'pattern_fill' },
    ];
    expect(
      phases.map((phase) => manualWorkspaceWork(phase).blocksLeaving),
    ).toEqual([true, true, true, false]);
    expect(manualWorkspaceWork(phases[3]).hasDraft).toBe(false);
  });

  it.each([
    'hasPreview',
    'hasPendingZone',
    'hasRowAxis',
    'placementAreaDrawing',
    'zoneDrawing',
  ] as const)(
    'retains the leaving boundary for a %s draft after a tool change',
    (draft) => {
      expect(manualWorkspaceWork({ ...idle, [draft]: true })).toMatchObject({
        hasDraft: true,
        blocksLeaving: true,
      });
    },
  );

  it('distinguishes initial unsaved zone drafts from saved zones on an existing plan', () => {
    expect(
      manualWorkspaceWork({ ...idle, initialZoneDrafts: 8 }).blocksLeaving,
    ).toBe(false);
    expect(
      manualWorkspaceWork({ ...idle, hasPlan: false, initialZoneDrafts: 1 }),
    ).toMatchObject({
      hasDraft: true,
      blocksLeaving: true,
    });
    expect(
      manualWorkspaceWork({ ...idle, hasPlan: false, tool: 'draw_area' })
        .blocksLeaving,
    ).toBe(true);
  });
});
