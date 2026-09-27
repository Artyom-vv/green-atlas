import { describe, expect, it } from 'vitest';
import {
  manualWorkspaceWork,
  type ManualWorkspaceInput,
} from './manualWorkspaceWork';

const idle: ManualWorkspaceInput = {
  tool: 'select',
  placementPanelOpen: false,
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
  it('allows the assistant after leaving placement despite a retained panel preference', () => {
    const placing = manualWorkspaceWork({
      ...idle,
      tool: 'pattern_fill',
      placementPanelOpen: true,
    });
    expect(placing.blocksAssistant).toBe(true);
    expect(placing.blocksLeaving).toBe(false);
    expect(
      manualWorkspaceWork({ ...idle, tool: 'select', placementPanelOpen: true })
        .blocksAssistant,
    ).toBe(false);
    expect(
      manualWorkspaceWork({
        ...idle,
        tool: 'pattern_fill',
        placementPanelOpen: false,
      }).blocksAssistant,
    ).toBe(false);
  });

  it('protects an unfinished row before an axis exists and releases after the drawing is cancelled', () => {
    const sketch = {
      ...idle,
      tool: 'pattern_row' as const,
      rowDrawingPoints: 1,
      hasRowAxis: false,
      placementPanelOpen: true,
    };
    expect(manualWorkspaceWork(sketch)).toMatchObject({
      hasDraft: true,
      blocksLeaving: true,
      blocksAssistant: true,
    });
    expect(
      manualWorkspaceWork({ ...sketch, rowDrawingPoints: 0 }),
    ).toMatchObject({
      hasDraft: false,
      blocksLeaving: false,
      blocksAssistant: false,
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
      blocksAssistant: true,
    });
    expect(
      manualWorkspaceWork({ ...live, brushDrawing: false, brushStrokes: 1 })
        .blocksAssistant,
    ).toBe(true);
    expect(
      manualWorkspaceWork({ ...live, brushDrawing: false }).blocksAssistant,
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
        blocksAssistant: true,
      });
      expect(manualWorkspaceWork({ ...active, [pending]: false })).toEqual({
        hasDraft: false,
        pending: false,
        blocksLeaving: false,
        blocksAssistant: false,
      });
    },
  );

  it('retains protection across drawing, zone review, saving and return to placement', () => {
    const phases: ManualWorkspaceInput[] = [
      { ...idle, tool: 'draw_area', placementAreaDrawing: true },
      { ...idle, hasPendingZone: true },
      { ...idle, hasPendingZone: true, mutationPending: true },
      { ...idle, tool: 'pattern_fill', placementPanelOpen: true },
    ];
    expect(
      phases.map((phase) => manualWorkspaceWork(phase).blocksAssistant),
    ).toEqual([true, true, true, true]);
    expect(
      phases.map((phase) => manualWorkspaceWork(phase).blocksLeaving),
    ).toEqual([true, true, true, false]);
    expect(
      manualWorkspaceWork({ ...phases[3], placementPanelOpen: false })
        .blocksAssistant,
    ).toBe(false);
  });

  it.each([
    'hasPreview',
    'hasPendingZone',
    'hasRowAxis',
    'placementAreaDrawing',
    'zoneDrawing',
  ] as const)(
    'retains the same leaving and assistant boundary for a %s draft after a tool change',
    (draft) => {
      expect(manualWorkspaceWork({ ...idle, [draft]: true })).toMatchObject({
        hasDraft: true,
        blocksLeaving: true,
        blocksAssistant: true,
      });
    },
  );

  it('distinguishes initial unsaved zone drafts from saved zones on an existing plan', () => {
    expect(
      manualWorkspaceWork({ ...idle, initialZoneDrafts: 8 }).blocksAssistant,
    ).toBe(false);
    expect(
      manualWorkspaceWork({ ...idle, hasPlan: false, initialZoneDrafts: 1 }),
    ).toMatchObject({
      hasDraft: true,
      blocksLeaving: true,
      blocksAssistant: true,
    });
    expect(
      manualWorkspaceWork({ ...idle, hasPlan: false, tool: 'draw_area' })
        .blocksAssistant,
    ).toBe(true);
  });

  it('does not let an assistant-owned proposal or preview become a manual blocker', () => {
    const assistantOwned = {
      ...idle,
      assistantProposal: { id: 'assistant-proposal' },
      assistantPreview: { id: 'assistant-preview' },
      assistantZonePreview: { id: 'zone-preview' },
      assistantPending: 'applying',
    };
    expect(manualWorkspaceWork(assistantOwned)).toEqual({
      hasDraft: false,
      pending: false,
      blocksLeaving: false,
      blocksAssistant: false,
    });
    expect(
      manualWorkspaceWork({ ...assistantOwned, hasPreview: true })
        .blocksAssistant,
    ).toBe(true);
  });
});
