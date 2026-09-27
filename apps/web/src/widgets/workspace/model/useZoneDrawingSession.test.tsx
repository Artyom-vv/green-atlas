import { useState } from 'react';
import { act, cleanup, fireEvent, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlantingZoneAssignment, Project } from '@green/api-client';
import type { MapTool } from '@/entities/editor';
import type { ZoneReviewDraft } from '@/features/planting-zones/model/zoneDrawing';
import Draw from 'ol/interaction/Draw';
import Map from 'ol/Map';
import View from 'ol/View';
import VectorSource from 'ol/source/Vector';
import { useZoneDrawingSession } from '@/features/planting-zones/model/useZoneDrawingSession';
import { manualWorkspaceWork } from '@/features/workspace/manualWorkspaceWork';
import { createViewportHandle } from '@/widgets/map/adapters/openlayers/createViewportHandle';
import { useWorkspaceKeyboard } from './useWorkspaceKeyboard';
import {
  viewportDrawingBindings,
  type ViewportDrawingProps,
} from './viewport/viewportDrawing';

const maps: Map[] = [];
beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    },
  );
});
afterEach(() => {
  cleanup();
  maps.splice(0).forEach((map) => map.dispose());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const original: PlantingZoneAssignment = {
  id: 'west',
  label: 'Западный участок',
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 0],
      ],
    ],
  },
};
const replacement = {
  type: 'Polygon',
  coordinates: [
    [
      [1, 1],
      [9, 1],
      [9, 9],
      [1, 1],
    ],
  ],
} satisfies { type: 'Polygon'; coordinates: number[][][] };

function setup() {
  const project = { planting_zones: [structuredClone(original)] } as Project;
  const source = new VectorSource();
  const nativeDraw = new Draw({ type: 'Polygon', source });
  const nativeMap = new Map({
    view: new View({ center: [0, 0], zoom: 1 }),
    controls: [],
    interactions: [],
  });
  maps.push(nativeMap);
  nativeMap.addInteraction(nativeDraw);
  const map = createViewportHandle({
    mapRef: { current: nativeMap },
    targetRef: { current: null },
    focusAbortRef: { current: undefined },
    baseSourceRef: { current: source },
    zoneSourceRef: { current: source },
    constraintSourceRef: { current: source },
    planSourceRef: { current: source },
    rowDrawingCountRef: { current: 0 },
    drawRef: { current: nativeDraw },
    fit: vi.fn(),
    fitPlan: vi.fn(),
  });
  const returnToOrigin = vi.fn();
  const setPendingZone = vi.fn();
  const setZoneReviewOpen = vi.fn();
  const result = renderHook(() => {
    const [tool, setTool] = useState<MapTool>('select');
    const drawing = useZoneDrawingSession({
      abortDrawing: map.abortDrawing,
      onToolChange: setTool,
      onReturn: (context) => {
        returnToOrigin(context);
        if (context.purpose === 'place')
          setTool(context.nextTool ?? 'pattern_fill');
      },
    });
    useWorkspaceKeyboard({ blocked: false, onEscape: drawing.cancel });
    const guard = manualWorkspaceWork({
      tool,
      placementPanelOpen: false,
      hasPlan: true,
      hasPreview: false,
      hasPendingZone: false,
      initialZoneDrafts: 1,
      brushStrokes: 0,
      brushDrawing: false,
      hasRowAxis: false,
      rowDrawingPoints: 0,
      placementAreaDrawing: drawing.session?.purpose === 'place',
      zoneDrawing: drawing.session?.purpose === 'manage',
      mutationPending: false,
      brushPreviewPending: false,
      createPlanPending: false,
      releasePending: false,
    });
    const bindings = viewportDrawingBindings({
      addMapArea: vi.fn(),
      draftZones: project.planting_zones!,
      editor: { setTool } as ViewportDrawingProps['editor'],
      planLocked: false,
      project,
      projectHasPlan: true,
      setPendingZone,
      finishZoneDrawing: drawing.finish,
      setZoneReviewOpen,
      zoneDrawingSession: drawing.session,
    });
    return { drawing, tool, guard, bindings };
  });
  return {
    ...result,
    nativeDraw,
    project,
    returnToOrigin,
    setPendingZone,
    setZoneReviewOpen,
  };
}

describe('managed zone drawing cancellation', () => {
  it.each(['Escape', 'cancel button'])(
    '%s releases the gesture and resource guards, preserving the saved contour',
    (method) => {
      const state = setup();
      act(() =>
        state.result.current.drawing.begin({
          target: 'west',
          purpose: 'manage',
        }),
      );
      state.nativeDraw.appendCoordinates([
        [2000, 2000],
        [2800, 2000],
      ]);
      expect(state.result.current.tool).toBe('draw_area');
      expect(state.result.current.guard.hasDraft).toBe(true);
      expect(
        state.nativeDraw.getOverlay().getSource()!.getFeatures().length,
      ).toBeGreaterThan(0);

      if (method === 'Escape') fireEvent.keyDown(window, { key: 'Escape' });
      else act(() => state.result.current.drawing.cancel());

      expect(state.result.current.drawing.session).toBeUndefined();
      expect(state.result.current.tool).toBe('select');
      expect(state.result.current.guard).toMatchObject({
        hasDraft: false,
        blocksLeaving: false,
      });
      expect(
        state.nativeDraw.getOverlay().getSource()!.getFeatures(),
      ).toHaveLength(0);
      expect(state.returnToOrigin).toHaveBeenCalledExactlyOnceWith({
        target: 'west',
        purpose: 'manage',
      });
      expect(state.project.planting_zones).toEqual([original]);
      expect(state.setPendingZone).not.toHaveBeenCalled();

      act(() =>
        state.result.current.drawing.begin({
          target: 'west',
          purpose: 'manage',
        }),
      );
      act(() => state.result.current.bindings.onDrawArea?.(replacement));
      expect(state.setPendingZone).toHaveBeenCalledExactlyOnceWith({
        zone: { ...original, geometry: replacement },
        purpose: 'manage',
      });
      expect(state.result.current.drawing.session).toBeUndefined();
      expect(state.setZoneReviewOpen).toHaveBeenCalledWith(true);
      expect(state.project.planting_zones).toEqual([original]);
    },
  );

  it('ignores a completed polygon delivered by the cancelled gesture', () => {
    const state = setup();
    act(() =>
      state.result.current.drawing.begin({ target: 'west', purpose: 'manage' }),
    );
    const cancelledBindings = state.result.current.bindings;
    act(() => {
      state.result.current.drawing.cancel();
      cancelledBindings.onDrawArea?.(replacement);
    });
    expect(state.setPendingZone).not.toHaveBeenCalled();
    expect(state.setZoneReviewOpen).not.toHaveBeenCalled();
    expect(state.project.planting_zones).toEqual([original]);
  });

  it('leaves Escape from editable controls and nested dialogs to their own task', () => {
    const state = setup();
    act(() =>
      state.result.current.drawing.begin({ target: 'west', purpose: 'manage' }),
    );
    const input = document.createElement('input');
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    document.body.append(input, dialog);
    fireEvent.keyDown(input, { key: 'Escape' });
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(state.result.current.drawing.session?.target).toBe('west');
    expect(state.returnToOrigin).not.toHaveBeenCalled();
  });
});

describe('placement zone drawing transitions', () => {
  it.each(['Escape', 'cancel button'])(
    '%s aborts placement drawing and restores its original row form without writing',
    (method) => {
      const state = setup();
      const context = {
        target: 'new',
        purpose: 'place',
        nextTool: 'pattern_row',
      } as const;
      act(() => state.result.current.drawing.begin(context));
      state.nativeDraw.appendCoordinates([
        [2000, 2000],
        [2800, 2000],
      ]);
      expect(state.result.current.guard.blocksLeaving).toBe(true);

      if (method === 'Escape') fireEvent.keyDown(window, { key: 'Escape' });
      else act(() => state.result.current.drawing.cancel());

      expect(state.result.current.drawing.session).toBeUndefined();
      expect(state.result.current.tool).toBe('pattern_row');
      expect(state.result.current.guard.hasDraft).toBe(false);
      expect(state.returnToOrigin).toHaveBeenCalledExactlyOnceWith(context);
      expect(
        state.nativeDraw.getOverlay().getSource()!.getFeatures(),
      ).toHaveLength(0);
      expect(state.setPendingZone).not.toHaveBeenCalled();
      expect(state.project.planting_zones).toEqual([original]);

      act(() => state.result.current.drawing.begin(context));
      act(() => state.result.current.bindings.onDrawArea?.(replacement));
      expect(state.setPendingZone).toHaveBeenCalledWith({
        zone: expect.objectContaining({ geometry: replacement }),
        purpose: 'place',
        nextTool: 'pattern_row',
      });
    },
  );

  it('discards a gesture for another tool without reopening its previous surface', () => {
    const state = setup();
    act(() =>
      state.result.current.drawing.begin({ target: 'new', purpose: 'place' }),
    );
    state.nativeDraw.appendCoordinates([
      [2000, 2000],
      [2800, 2000],
    ]);
    act(() => state.result.current.drawing.discard());
    expect(state.result.current.drawing.session).toBeUndefined();
    expect(state.result.current.guard.blocksLeaving).toBe(false);
    expect(
      state.nativeDraw.getOverlay().getSource()!.getFeatures(),
    ).toHaveLength(0);
    expect(state.returnToOrigin).not.toHaveBeenCalled();
    expect(state.setPendingZone).not.toHaveBeenCalled();
  });

  it.each(['manage', 'place'] as const)(
    'redrawing a %s review preserves purpose, identity and metadata through the next review',
    (purpose) => {
      const state = setup();
      const draft: ZoneReviewDraft = {
        zone: { ...original, id: 'unsaved-east', label: 'Восточный участок' },
        purpose,
        ...(purpose === 'place' && { nextTool: 'pattern_row' as const }),
      };
      act(() => state.result.current.drawing.redraw(draft));
      act(() => state.result.current.bindings.onDrawArea?.(replacement));
      expect(state.setPendingZone).toHaveBeenCalledExactlyOnceWith({
        ...draft,
        zone: { ...draft.zone, geometry: replacement },
      });
      expect(draft.zone.geometry).toEqual(original.geometry);
      expect(state.project.planting_zones).toEqual([original]);
    },
  );

  it('ignores a previous gesture callback after drawing has restarted', () => {
    const state = setup();
    act(() =>
      state.result.current.drawing.begin({ target: 'west', purpose: 'manage' }),
    );
    const previous = state.result.current.bindings;
    act(() => state.result.current.drawing.cancel());
    act(() =>
      state.result.current.drawing.begin({ target: 'new', purpose: 'place' }),
    );
    act(() => previous.onDrawArea?.(replacement));
    expect(state.setPendingZone).not.toHaveBeenCalled();
    expect(state.result.current.drawing.session?.purpose).toBe('place');
    act(() => state.result.current.bindings.onDrawArea?.(replacement));
    expect(state.setPendingZone).toHaveBeenCalledOnce();
  });

  it('returns the unchanged review draft when its redraw gesture is cancelled', () => {
    const state = setup();
    const review: ZoneReviewDraft = {
      zone: structuredClone(original),
      purpose: 'place',
      nextTool: 'pattern_row',
    };
    act(() => state.result.current.drawing.redraw(review));
    state.nativeDraw.appendCoordinates([
      [2000, 2000],
      [2800, 2000],
    ]);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(state.returnToOrigin).toHaveBeenCalledExactlyOnceWith({
      ...review,
      target: original.id,
    });
    expect(review.zone.geometry).toEqual(original.geometry);
    expect(
      state.nativeDraw.getOverlay().getSource()!.getFeatures(),
    ).toHaveLength(0);
    expect(state.setPendingZone).not.toHaveBeenCalled();
  });
});
