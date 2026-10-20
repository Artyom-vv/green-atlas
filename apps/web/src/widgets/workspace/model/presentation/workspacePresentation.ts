import { useMemo } from 'react';
import type {
  Project,
  Layer,
  PlanObject,
  ValidationIssue,
} from '@green/api-client';
import {
  currentGrowthIssues,
  objectsWithIssues,
} from '@/features/growth-scenario/model/growthScenario';
import type { EditorSessionState } from '@/entities/editor/model/editorTypes';
import { metadataOnlyObjectIds } from '@/entities/planting/model/planPresentation';
import { zoneCollection } from '@/features/workspace/zoneSelection';
import { initialWorkspaceExtent } from '../viewport/initialWorkspaceExtent';
const EMPTY_PLAN_OBJECTS: PlanObject[] = [];
const EMPTY_LAYERS: Layer[] = [];

import type { MapTool } from '@/entities/editor';
import type { IdeRightTab } from '@/widgets/workbench';
export const ideTabForTool = (
  tool: MapTool,
  placementOwnsContext = false,
): IdeRightTab =>
  placementOwnsContext ||
  ['pattern_row', 'pattern_fill', 'brush', 'add_tree', 'add_shrub'].includes(
    tool,
  )
    ? 'tool'
    : 'inspector';

/** Derived presentation of the current server project and editor selection.
 * No query, cached copy, draft or gesture lifecycle is owned here. */
export function useWorkspacePresentation(
  project: Project | undefined,
  {
    activeLayerId,
    visibility,
    selectedIds,
  }: Pick<EditorSessionState, 'activeLayerId' | 'visibility' | 'selectedIds'>,
  selectedPatternZoneIds: string[],
  currentForecastIssues?: ValidationIssue[],
) {
  const layers = project?.layers ?? EMPTY_LAYERS;
  const savedObjects = project?.plan?.objects ?? EMPTY_PLAN_OBJECTS;
  const issues = useMemo(
    () =>
      currentGrowthIssues(project?.plan?.issues ?? [], currentForecastIssues),
    [project?.plan?.issues, currentForecastIssues],
  );
  const planObjects = useMemo(
    () =>
      currentForecastIssues
        ? objectsWithIssues(savedObjects, issues)
        : savedObjects,
    [savedObjects, issues, currentForecastIssues],
  );
  const sourceWarnings = project?.source_file?.warnings ?? [];
  const projectHasPlan = Boolean(project?.plan);
  const sourcePreview = Boolean(
    project &&
    (!project.map_ready || project.import_status?.editability === 'read_only'),
  );
  const planLocked = project?.import_status?.editability === 'read_only';
  const initialExtent = useMemo(
    () =>
      initialWorkspaceExtent(
        project?.source_file?.bounds,
        layers,
        Boolean(sourcePreview),
      ),
    [project?.source_file?.bounds, layers, sourcePreview],
  );
  const activeLayer = useMemo(
    () => layers.find((layer) => layer.id === activeLayerId),
    [activeLayerId, layers],
  );
  const hiddenLayerNames = useMemo(
    () =>
      layers
        .filter((layer) => visibility[layer.id] === false)
        .map((layer) => layer.source_name),
    [layers, visibility],
  );
  const selectedId = selectedIds.length === 1 ? selectedIds[0] : undefined;
  const selectedObject = useMemo(
    () => planObjects.find((item) => item.id === selectedId),
    [planObjects, selectedId],
  );
  const selectedPatternZones = useMemo(
    () =>
      (project?.planting_zones ?? []).filter(
        (zone) => zone.id && selectedPatternZoneIds.includes(zone.id),
      ),
    [project?.planting_zones, selectedPatternZoneIds],
  );
  const focusGeometry = useMemo(
    () => zoneCollection(selectedPatternZones),
    [selectedPatternZones],
  );
  const brushZones = selectedPatternZones;
  const plantingZoneIds = useMemo(
    () =>
      (project?.planting_zones ?? []).flatMap((zone) =>
        zone.id ? [zone.id] : [],
      ),
    [project?.planting_zones],
  );
  const plantingZoneUsage = useMemo(
    () =>
      planObjects.reduce<Record<string, number>>((usage, object) => {
        if (object.planting_zone_id)
          usage[object.planting_zone_id] =
            (usage[object.planting_zone_id] ?? 0) + 1;
        return usage;
      }, {}),
    [planObjects],
  );
  const selectedObjects = useMemo(() => {
    const ids = new Set(selectedIds);
    return planObjects.filter((item) => item.id && ids.has(item.id));
  }, [planObjects, selectedIds]);
  const metadataOnlyIds = useMemo(
    () => metadataOnlyObjectIds(project?.plan?.issues ?? []),
    [project?.plan?.issues],
  );
  const selectedLocked = selectedObjects.some((object) => object.locked);
  return {
    layers,
    planObjects,
    sourceWarnings,
    projectHasPlan,
    sourcePreview,
    planLocked,
    initialExtent,
    activeLayer,
    hiddenLayerNames,
    selectedObject,
    selectedPatternZones,
    focusGeometry,
    brushZones,
    plantingZoneIds,
    plantingZoneUsage,
    selectedObjects,
    issues,
    metadataOnlyIds,
    selectedLocked,
  };
}
