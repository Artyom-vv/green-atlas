import { useCallback, useLayoutEffect, useRef, type RefObject } from 'react';
import type {
  EditorPanel,
  EditorRightTab,
  MapTool,
  SelectionMode,
  StateUpdate,
} from '@/entities/editor';
import type { EditorSessionActions } from '@/entities/editor/model/editorStore';
import { assignmentFromGeometry } from '@/entities/planting-zone/model/plantingZones';
import type { useZoneCommands } from '@/features/planting-zones';
import type { PlanObject, PlantingZoneAssignment } from '@green/api-client';
import type {
  MapAreaTarget,
  MapHoverItem,
  MapHoverTarget,
  MapViewportHandle,
} from '@/widgets/map/model/mapContracts';
import type { SceneReviewHandle } from '@/widgets/scene/model/sceneContracts';

export interface WorkspaceSelectionOptions {
  projectId: string;
  projectHasPlan: boolean;
  zoneCount: number;
  planObjects: Pick<PlanObject, 'id'>[];
  tool: MapTool;
  planLocked: boolean;
  panel: EditorPanel;
  savePlacementZone: Pick<
    ReturnType<typeof useZoneCommands>['savePlacementZone'],
    'isPending' | 'mutate'
  >;
  setDraftZones: (update: StateUpdate<PlantingZoneAssignment[]>) => void;
  setPanel: EditorSessionActions['setPanel'];
  openRightPanel: () => void;
  setMapHoverTarget: (update: StateUpdate<MapHoverTarget | undefined>) => void;
  setMapInspectTarget: (
    update: StateUpdate<MapHoverTarget | undefined>,
  ) => void;
  setMapAreaTarget: (update: StateUpdate<MapAreaTarget | undefined>) => void;
  setActiveLayerId: EditorSessionActions['setActiveLayerId'];
  setSelectedPatternZoneIds: (update: StateUpdate<string[]>) => void;
  clearSelection: EditorSessionActions['clearSelection'];
  select: EditorSessionActions['select'];
  selectionBlocked: boolean;
  activateTool: (tool: MapTool) => void;
  setIdeRightTab: (tab: EditorRightTab) => void;
  sceneOpen: boolean;
  sceneReview: RefObject<Pick<SceneReviewHandle, 'fitSelection'> | null>;
  mapViewport: RefObject<Pick<MapViewportHandle, 'fitObjects'> | null>;
}

export function useWorkspaceSelection({
  projectId,
  projectHasPlan,
  zoneCount,
  planObjects,
  tool,
  planLocked,
  panel,
  savePlacementZone,
  setDraftZones,
  setPanel,
  openRightPanel,
  setMapHoverTarget,
  setMapInspectTarget,
  setMapAreaTarget,
  setActiveLayerId,
  setSelectedPatternZoneIds,
  clearSelection,
  select,
  selectionBlocked,
  activateTool,
  setIdeRightTab,
  sceneOpen,
  sceneReview,
  mapViewport,
}: WorkspaceSelectionOptions) {
  const focusFrame = useRef<number | undefined>(undefined);
  useLayoutEffect(
    () => () => {
      if (focusFrame.current !== undefined)
        cancelAnimationFrame(focusFrame.current);
      focusFrame.current = undefined;
    },
    [projectId],
  );
  const addMapArea = useCallback(
    (
      geometry: PlantingZoneAssignment['geometry'],
      label: string,
      sourceId?: string,
    ) => {
      setDraftZones((current) => {
        if (sourceId && current.some((zone) => zone.id === sourceId))
          return current;
        return [
          ...current,
          assignmentFromGeometry(geometry, current.length + 1, label, sourceId),
        ];
      });
      setPanel('zones');
      openRightPanel();
    },
    [openRightPanel, setPanel, setDraftZones],
  );
  const handleMapArea = useCallback(
    (target: MapAreaTarget, mode: SelectionMode = 'replace') => {
      // The picker is map-local state. Clear it as soon as a target is chosen,
      // while leaving the task inspector mounted for an active placement tool.
      setMapHoverTarget(undefined);
      setMapInspectTarget(undefined);
      if (!projectHasPlan) {
        if (target.selectable && target.geometry)
          addMapArea(target.geometry, target.label, target.sourceId);
        return;
      }
      if (
        !target.plantingZoneId &&
        target.selectable &&
        target.geometry &&
        ['pattern_fill', 'pattern_row', 'brush'].includes(tool) &&
        !planLocked &&
        !savePlacementZone.isPending
      ) {
        const zone = assignmentFromGeometry(
          target.geometry,
          zoneCount + 1,
          target.label,
          target.sourceId,
        );
        setMapAreaTarget(undefined);
        savePlacementZone.mutate({
          zone,
          nextTool:
            tool === 'pattern_row' || tool === 'brush' ? tool : 'pattern_fill',
        });
        return;
      }
      if (panel === 'zones') {
        setMapAreaTarget(target);
        return;
      }
      if (target.plantingZoneId) {
        setSelectedPatternZoneIds((current) => {
          if (mode === 'add')
            return [...new Set([...current, target.plantingZoneId!])];
          if (mode === 'subtract')
            return current.filter((id) => id !== target.plantingZoneId);
          return [target.plantingZoneId!];
        });
      }
      const taskInspectorActive =
        tool === 'pattern_fill' ||
        tool === 'pattern_row' ||
        tool === 'brush' ||
        tool === 'draw_area';
      setMapAreaTarget(taskInspectorActive ? undefined : target);
      setActiveLayerId(undefined);
      // Pattern/row/brush inspectors are the current task and must survive a
      // map target choice. Selection mode still clears object selection so the
      // inspector cannot silently describe a different task.
      if (!taskInspectorActive) setPanel(null);
      clearSelection();
      openRightPanel();
    },
    [
      addMapArea,
      clearSelection,
      openRightPanel,
      panel,
      planLocked,
      zoneCount,
      projectHasPlan,
      savePlacementZone,
      setMapAreaTarget,
      setMapHoverTarget,
      setMapInspectTarget,
      setSelectedPatternZoneIds,
      setActiveLayerId,
      setPanel,
      tool,
    ],
  );
  const handleMapStackSelect = useCallback(
    (item: MapHoverItem) => {
      if (item.target) {
        handleMapArea(item.target);
        return;
      }
      if (!item.preview) return;
      setActiveLayerId(undefined);
      setPanel(null);
      openRightPanel();
      if (planObjects.some((object) => object.id === item.preview!.objectId))
        select([item.preview.objectId], 'replace');
    },
    [
      select,
      handleMapArea,
      openRightPanel,
      planObjects,
      setActiveLayerId,
      setPanel,
    ],
  );
  const selectFromExplorer = (ids: string[], fit = true) => {
    if (selectionBlocked) return;
    activateTool('select');
    select(ids, 'replace');
    setActiveLayerId(undefined);
    setPanel(null);
    setMapAreaTarget(undefined);
    openRightPanel();
    setIdeRightTab('inspector');
    if (fit) {
      if (focusFrame.current !== undefined)
        cancelAnimationFrame(focusFrame.current);
      focusFrame.current = requestAnimationFrame(() => {
        focusFrame.current = undefined;
        if (sceneOpen) sceneReview.current?.fitSelection();
        else mapViewport.current?.fitObjects(ids);
      });
    }
  };
  return {
    addMapArea,
    handleMapArea,
    handleMapStackSelect,
    selectFromExplorer,
  };
}
