import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MapAreaTarget } from '@/widgets/map/model/mapContracts';
import {
  useWorkspaceSelection,
  type WorkspaceSelectionOptions,
} from './useWorkspaceSelection';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const area: MapAreaTarget = {
  sourceId: 'source-a',
  kind: 'existing_green',
  label: 'Зелёная зона',
  detail: 'Сохраняемый контур',
  selectable: true,
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
function fixture(): WorkspaceSelectionOptions {
  return {
    projectId: 'project-1',
    projectHasPlan: true,
    zoneCount: 1,
    planObjects: [{ id: 'tree-1' }],
    tool: 'select',
    planLocked: false,
    panel: null,
    savePlacementZone: { isPending: false, mutate: vi.fn() },
    setDraftZones: vi.fn(),
    setPanel: vi.fn(),
    openRightPanel: vi.fn(),
    setMapHoverTarget: vi.fn(),
    setMapInspectTarget: vi.fn(),
    setMapAreaTarget: vi.fn(),
    setActiveLayerId: vi.fn(),
    setSelectedPatternZoneIds: vi.fn(),
    clearSelection: vi.fn(),
    select: vi.fn(),
    selectionBlocked: false,
    activateTool: vi.fn(),
    setIdeRightTab: vi.fn(),
    sceneOpen: false,
    sceneReview: { current: { fitSelection: vi.fn() } },
    mapViewport: { current: { fitObjects: vi.fn() } },
  };
}

describe('workspace selection callbacks', () => {
  it('keeps a placement inspector and saves a source contour through the existing zone command', () => {
    const options = { ...fixture(), tool: 'pattern_row' as const };
    const { result } = renderHook(() => useWorkspaceSelection(options));
    act(() => result.current.handleMapArea(area));
    expect(options.savePlacementZone.mutate).toHaveBeenCalledExactlyOnceWith({
      zone: expect.objectContaining({
        geometry: area.geometry,
        label: area.label,
      }),
      nextTool: 'pattern_row',
    });
    expect(options.setMapHoverTarget).toHaveBeenCalledWith(undefined);
    expect(options.setMapInspectTarget).toHaveBeenCalledWith(undefined);
    expect(options.setPanel).not.toHaveBeenCalled();
  });

  it('changes existing zone selection without closing the active task', () => {
    const options = { ...fixture(), tool: 'brush' as const };
    const { result } = renderHook(() => useWorkspaceSelection(options));
    act(() =>
      result.current.handleMapArea(
        { ...area, plantingZoneId: 'zone-2' },
        'add',
      ),
    );
    const update = vi.mocked(options.setSelectedPatternZoneIds).mock
      .calls[0][0];
    expect(
      typeof update === 'function' && update(['zone-1', 'zone-2']),
    ).toEqual(['zone-1', 'zone-2']);
    expect(options.clearSelection).toHaveBeenCalledTimes(1);
    expect(options.setMapAreaTarget).toHaveBeenCalledWith(undefined);
    expect(options.setPanel).not.toHaveBeenCalled();
    expect(options.savePlacementZone.mutate).not.toHaveBeenCalled();
  });

  it('does not change selection when a draft blocks the explorer', () => {
    const options = { ...fixture(), selectionBlocked: true };
    const { result } = renderHook(() => useWorkspaceSelection(options));
    act(() => result.current.selectFromExplorer(['tree-1']));
    expect(options.activateTool).not.toHaveBeenCalled();
    expect(options.select).not.toHaveBeenCalled();
    expect(options.mapViewport.current?.fitObjects).not.toHaveBeenCalled();
  });

  it('cancels deferred framing when another project takes over', () => {
    const options = fixture();
    const cancelled = vi.spyOn(window, 'cancelAnimationFrame');
    vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(41);
    const { result, rerender } = renderHook(useWorkspaceSelection, {
      initialProps: options,
    });
    act(() => result.current.selectFromExplorer(['tree-1']));
    expect(options.select).toHaveBeenCalledExactlyOnceWith(
      ['tree-1'],
      'replace',
    );
    rerender({ ...options, projectId: 'project-2' });
    expect(cancelled).toHaveBeenCalledWith(41);
    expect(options.mapViewport.current?.fitObjects).not.toHaveBeenCalled();
  });
});
