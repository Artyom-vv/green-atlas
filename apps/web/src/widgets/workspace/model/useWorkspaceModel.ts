import { useWorkspacePlacementDraft } from './useWorkspacePlacementDraft';
import { useCandidateInspection } from '@/features/placement/inspection/model/useCandidateInspection';
import { useWorkspaceCadSource } from './viewport/useWorkspaceCadSource';
import { useWorkspaceRefresh } from '@/features/workspace/api/useWorkspaceRefresh';
import { useWorkspaceNavigation } from './navigation/useWorkspaceNavigation';
import { workspacePersistenceState } from '@/features/workspace/model/workspacePersistenceState';
import { acknowledgeProjectWrite } from '@/entities/project/model/acknowledgedRead';
import { projectPreparationNotice } from '@/entities/project/model/projectResume';
import { useMoveValidation } from '@/features/plan-changes/model/useMoveValidation';
import {
  workspaceProjectQuery,
  workspaceHistoryQuery,
  workspaceSpeciesQuery,
  workspaceZonePreviewQuery,
} from '@/features/workspace/api/workspaceQueries';
import {
  selectionSpeciesQuery,
  zoneSpeciesQuery,
  placementMasksQueryOptions,
} from '@/features/workspace/api/placementQueries';
import type {
  EditorPanel as WorkspacePanel,
  EditorRightTab as IdeRightTab,
} from '@/entities/editor';
import { useEditorSession } from '@/entities/editor';
import { type GrowthHorizon } from '@/entities/planting-forecast';
import { useGrowthScenario } from '@/features/growth-scenario/model/useGrowthScenario';
import {
  currentGrowthIssues,
  objectsWithIssues,
} from '@/features/growth-scenario/model/growthScenario';
import { groupTransformDraft } from '@/entities/planting/model/groupTransform';
import { useSinglePlacement } from '@/features/placement';
import {
  useDeleteSelection,
  usePlanChangeCommit,
} from '@/features/plan-changes';
import { usePlanHistoryCommands } from '@/features/plan-history/model/usePlanHistoryCommands';
import { useZoneCommands } from '@/features/planting-zones';
import { useZoneDrawingSession } from '@/features/planting-zones/model/useZoneDrawingSession';
import type {
  ZoneDrawingContext,
  ZoneReviewDraft,
} from '@/features/planting-zones/model/zoneDrawing';
import { useProjectRelease } from '@/features/project-release/model/useProjectRelease';
import { useStartWorkspace } from '@/features/start-workspace/model/useStartWorkspace';
import { resolveInspectorView } from '@/features/workspace/inspectorView';
import { manualWorkspaceWork } from '@/features/workspace/manualWorkspaceWork';
import {
  placementZoneSelection,
  zoneCollection,
  zoneExtent,
} from '@/features/workspace/zoneSelection';
import { useViewportGeometry } from '@/widgets/map/api/useViewportGeometry';
import {
  type MapAreaTarget,
  type MapHoverTarget,
  type MapViewportHandle,
} from '@/widgets/map/model/mapContracts';
import { type MapTool } from '@/widgets/map/ui/MapToolbar';
import { type PlantingZoneAssignment } from '@green/api-client';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { sourceIdentity } from '@/features/source-preparation/model/sourcePreparation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ideTabForTool,
  useWorkspacePresentation,
} from './presentation/workspacePresentation';
import { useWorkspaceKeyboard } from './navigation/useWorkspaceKeyboard';
import { useWorkspaceSelection } from './useWorkspaceSelection';
import { usePlanComparison } from './viewport/usePlanComparison';
import { useWorkspaceSceneSession } from './viewport/useWorkspaceSceneSession';
import {
  WORKSPACE_TOOL_NAMES,
  workspaceToolHint,
} from './presentation/workspaceToolPresentation';
type WorkspaceDestination = Extract<
  WorkspacePanel,
  'zones' | 'plantings' | 'issues'
>;
export function useWorkspaceModel(projectId: string) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const projectQuery = useQuery(workspaceProjectQuery(projectId));
  const project = projectQuery.data;
  const preparationIssue = projectPreparationNotice(project);
  const projectReadPending =
    projectQuery.isFetching ||
    projectQuery.isError ||
    Boolean(preparationIssue);
  const cad = useWorkspaceCadSource(project);
  const mapViewport = useRef<MapViewportHandle>(null);

  const mapEditInFlightRef = useRef(false);
  const initializedProjectRef = useRef<string | undefined>(undefined);
  const [pendingTool, setPendingTool] = useState<MapTool>();
  const [deleteSelectionOpen, setDeleteSelectionOpen] = useState(false);
  const editor = useEditorSession((state) => state);
  const comparisonActive =
    editor.planComparison !== 'editing' &&
    Boolean(project?.plan && project.map_ready);
  const {
    tool,
    selectedIds,
    panel,
    setPanel,
    ideRightTab,
    setIdeRightTab,
    leftOpen,
    setLeftOpen,
    rightOpen,
    setRightOpen,
    resultsOpen,
    setResultsOpen,
    resultsTab,
    setResultsTab,
    activeLayerId,
    setActiveLayerId,
    visibility,
    setVisibility,
  } = editor;
  const setMapTool = editor.setTool;
  const setPlanComparison = editor.setPlanComparison;
  const changeIdeRightTab = (next: IdeRightTab) => {
    setRightOpen(true);
    setIdeRightTab(next);
  };
  const [mapHoverTarget, setMapHoverTarget] = useState<MapHoverTarget>();
  const [mapInspectTarget, setMapInspectTarget] = useState<MapHoverTarget>();
  const [mapAreaTarget, setMapAreaTarget] = useState<MapAreaTarget>();

  const [draftZones, setDraftZones] = useState<PlantingZoneAssignment[]>([]);
  const [zonePendingDelete, setZonePendingDelete] =
    useState<PlantingZoneAssignment>();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [dismissedOperationError, setDismissedOperationError] =
    useState<unknown>();
  const placement = useWorkspacePlacementDraft({
    projectId,
    project,
    tool,
    mapViewport,
    onSuggestionAccepted: () => {
      setActiveLayerId(undefined);
      setPanel(null);
      setRightOpen(true);
    },
    onChangesAccepted: () => {
      setReviewOpen(true);
      editor.setTool('select');
      setActiveLayerId(undefined);
      setPanel(null);
      setRightOpen(true);
    },
  });
  const {
    rowAxis,
    setRowAxis,
    rowInputMode,
    setRowInputMode,
    rowDrawingPoints,
    setRowAxisSource,
    selectedPatternZoneIds,
    setSelectedPatternZoneIds,
    recommendationOpen,
    setRecommendationOpen,
    brushStrokes,
    setBrushStrokes,
    brushDrawing,
    speciesAssignmentOpen,
    setSpeciesAssignmentOpen,
    setPatternSettingsOpen,
    singleTreeSpecies,
    singleShrubSpecies,
    previewPattern,
    previewRecommendation,
    previewAutomatic,
    previewBrush,
    previewChanges,
    patternPreview,
    recommendationPreview,
    automaticPreview,
    brushPreview,
    changePreview,
    resetPlacementPreviews,
  } = placement;
  const [pendingZone, setPendingZone] = useState<ZoneReviewDraft>();
  const [zoneReviewOpen, setZoneReviewOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const returnFromZoneDrawing = useCallback(
    (context: ZoneDrawingContext) => {
      if (context.purpose === 'manage') {
        setMapTool('select');
        setPanel('zones');
        return;
      }
      setPanel(null);
      setMapTool(context.nextTool ?? 'pattern_fill');
      setPatternSettingsOpen(true);
      setRightOpen(true);
    },
    [setMapTool, setPanel, setPatternSettingsOpen, setRightOpen],
  );
  const {
    session: zoneDrawingSession,
    begin: beginDrawingSession,
    redraw: redrawDrawingSession,
    finish: finishZoneDrawing,
    cancel: cancelZoneDrawing,
    discard: discardZoneDrawing,
  } = useZoneDrawingSession({
    abortDrawing: () => mapViewport.current?.abortDrawing(),
    onToolChange: setMapTool,
    onReturn: ({ zone, purpose, nextTool }) => {
      if (zone) {
        setPendingZone({ zone, purpose, nextTool });
        setZoneReviewOpen(true);
      } else returnFromZoneDrawing({ purpose, nextTool });
    },
  });
  const zoneDrawingMode =
    zoneDrawingSession?.purpose === 'manage'
      ? zoneDrawingSession.target
      : undefined;
  const placementAreaDrawing = zoneDrawingSession?.purpose === 'place';
  const placementOwnsContext =
    placementAreaDrawing || pendingZone?.purpose === 'place';
  useEffect(() => {
    setIdeRightTab(ideTabForTool(tool, placementOwnsContext));
    setRightOpen(true);
  }, [tool, placementOwnsContext, setIdeRightTab, setRightOpen]);
  const beginZoneDrawing = (target: string) =>
    beginDrawingSession({ target, purpose: 'manage' });
  const beginPlacementZoneDrawing = () => {
    setPatternSettingsOpen(false);
    setMapAreaTarget(undefined);
    beginDrawingSession({
      target: 'new',
      purpose: 'place',
      nextTool: tool === 'pattern_row' ? 'pattern_row' : 'pattern_fill',
    });
  };
  const redrawZone = () => {
    if (!pendingZone) return;
    redrawDrawingSession(pendingZone);
    setPendingZone(undefined);
    setZoneReviewOpen(false);
    setPanel(pendingZone.purpose === 'manage' ? 'zones' : null);
  };
  const cancelZoneReview = () => {
    if (!pendingZone) return;
    setPendingZone(undefined);
    setZoneReviewOpen(false);
    returnFromZoneDrawing(pendingZone);
  };
  const candidateInspection = useCandidateInspection({
    project,
    preview: patternPreview,
    zoneIds: selectedPatternZoneIds,
    onFocus: ([x, y]) => {
      if (sceneOpen) changeMapMode('2d');
      mapViewport.current?.fitGeometry({
        type: 'Polygon',
        coordinates: [
          [
            [x - 15, y - 15],
            [x + 15, y - 15],
            [x + 15, y + 15],
            [x - 15, y + 15],
            [x - 15, y - 15],
          ],
        ],
      });
    },
    onApply: (result) => {
      applyChanges.mutate(result);
    },
  });
  const growthHorizon = editor.growthHorizon;
  const [growthScenarioOpen, setGrowthScenarioOpen] = useState(
    () => editor.growthHorizon > 0,
  );
  const updateGrowthHorizon = editor.setGrowthHorizon;
  const setGrowthHorizon = useCallback(
    (value: GrowthHorizon) => {
      setGrowthScenarioOpen(true);
      updateGrowthHorizon(value ?? 0);
    },
    [updateGrowthHorizon],
  );
  const [pendingScene, setPendingScene] = useState(false);
  const [mapRenderMode, setMapRenderMode] = useState<'design' | 'cad'>(
    'design',
  );
  const [mapPanActive, setMapPanActive] = useState(false);
  const prepareSceneEntry = useCallback(() => {
    setPlanComparison('editing');
    discardZoneDrawing();
    setMapTool('select');
    setPanel(null);
    setRecommendationOpen(false);
    setActiveLayerId(undefined);
    setMapPanActive(false);
  }, [
    discardZoneDrawing,
    setPlanComparison,
    setActiveLayerId,
    setMapTool,
    setPanel,
    setRecommendationOpen,
  ]);
  const setSceneHorizon = setGrowthHorizon;
  useEffect(() => setMapInspectTarget(undefined), [tool]);
  const releaseState = useProjectRelease(projectId, {
    plan: project?.plan ?? undefined,
    geometryVersion: project?.geometry_version,
    initialHorizon: growthHorizon,
  });
  const { release, createRelease } = releaseState;
  const forecast = useGrowthScenario(
    project,
    growthHorizon,
    growthScenarioOpen && !comparisonActive,
  );
  const forecastActive = growthScenarioOpen && !comparisonActive;
  const presentation = useWorkspacePresentation(
    project,
    editor,
    selectedPatternZoneIds,
    forecast.currentIssues,
  );
  const {
    planObjects,
    projectHasPlan,
    sourcePreview,
    planLocked,
    activeLayer,
    plantingZoneIds,
    selectedObjects,
    selectedLocked,
  } = presentation;
  const mapScenarioIssues = useMemo(
    () =>
      forecastActive && forecast.data
        ? currentGrowthIssues(presentation.issues, forecast.data.issues)
        : presentation.issues,
    [forecastActive, forecast.data, presentation.issues],
  );
  const mapScenarioObjects = useMemo(
    () =>
      forecastActive && forecast.data
        ? objectsWithIssues(planObjects, mapScenarioIssues)
        : planObjects,
    [forecastActive, forecast.data, planObjects, mapScenarioIssues],
  );
  const historyQuery = useQuery(
    workspaceHistoryQuery(projectId, projectHasPlan && !planLocked),
  );
  const speciesQuery = useQuery(workspaceSpeciesQuery());
  const {
    query: mapGeometryQuery,
    pending: mapGeometryPending,
    delivery: mapGeometryDelivery,
    metadata: mapGeometryMetadata,
    onExtentChange: handleMapExtent,
  } = useViewportGeometry({
    projectId,
    geometryVersion: project?.geometry_version,
    sourceKey: sourceIdentity(project),
    enabled: cad.vectorGeometryEnabled,
  });
  const [geometryFrame, setGeometryFrame] = useState<{
    geometry: Record<string, unknown> | undefined;
    ready: boolean;
  }>();
  const onGeometryRenderState = useCallback(
    (geometry: Record<string, unknown> | undefined, ready: boolean) =>
      setGeometryFrame({ geometry, ready }),
    [],
  );
  const currentGeometry = mapGeometryDelivery?.feature_collection;
  const geometryDrawing = Boolean(
    currentGeometry &&
    (geometryFrame?.geometry !== currentGeometry || !geometryFrame?.ready),
  );
  const {
    changeMapMode,
    sceneHorizon,
    sceneInitialViewState,
    sceneMounted,
    sceneOpen,
    sceneQuery,
    sceneRequestHorizon,
    sceneReview,
    sceneViewStateRef,
  } = useWorkspaceSceneSession({
    projectId,
    project,
    horizon: growthHorizon,
    mapViewport,
    onBeforeOpen: prepareSceneEntry,
  });
  const refresh = useWorkspaceRefresh(projectId);
  useEffect(() => {
    if (!project || initializedProjectRef.current === project.id) return;
    initializedProjectRef.current = project.id;
    const projectZones = project.planting_zones ?? [];
    setDraftZones(projectZones);
    setSelectedPatternZoneIds(
      projectZones.flatMap((zone) => (zone.id ? [zone.id] : [])),
    );
    setPanel(project.plan ? null : 'zones');
    // Initial framing is owned by MapViewport, after its sources are ready.
  }, [project, setPanel, setSelectedPatternZoneIds]);

  const selectPatternZones = useCallback(
    (ids: string[]) => {
      setSelectedPatternZoneIds([...new Set(ids)]);
      setMapAreaTarget(undefined);
    },
    [setSelectedPatternZoneIds],
  );
  const focusZones = (zones: PlantingZoneAssignment[]) => {
    const extent = zoneExtent(zones);
    if (sceneOpen && extent) sceneReview.current?.fitExtent(extent);
    else if (zones.length)
      mapViewport.current?.fitGeometry(zoneCollection(zones));
  };
  const {
    check: previewSelectionMoveLive,
    clear: clearMoveValidation,
    validation: moveLiveCheck,
  } = useMoveValidation({
    projectId,
    planVersion: project?.plan?.version,
    stateVersion: project?.state_version,
    geometryVersion: project?.geometry_version,
    objects: selectedObjects,
    active: !planLocked && (tool === 'move' || tool === 'select'),
  });

  const speciesShortlistQuery = useQuery(
    selectionSpeciesQuery(
      projectId,
      selectedIds,
      Boolean(
        speciesAssignmentOpen &&
        selectedIds.length &&
        new Set(selectedObjects.map((object) => object.kind)).size === 1 &&
        !selectedLocked,
      ),
    ),
  );

  const zoneSpeciesShortlistQuery = useQuery(
    zoneSpeciesQuery(
      projectId,
      selectedPatternZoneIds,
      Boolean(
        project?.plan &&
        selectedPatternZoneIds.length &&
        (tool === 'pattern_fill' || tool === 'pattern_row'),
      ),
    ),
  );
  const placementMasksQuery = useQuery(
    placementMasksQueryOptions(
      projectId,
      Boolean(project?.plan && tool === 'pattern_fill'),
    ),
  );
  const speciesNames = useMemo(
    () =>
      new Map(
        (speciesQuery.data ?? []).map((item) => [item.id, item.common_name]),
      ),
    [speciesQuery.data],
  );
  const openRightPanel = useCallback(() => {
    setRightOpen(true);
  }, [setRightOpen]);
  const closeRightPanel = useCallback(
    () => setRightOpen(false),
    [setRightOpen],
  );
  const navigateWorkspace = useCallback(
    (destination: WorkspaceDestination) => {
      if (destination === 'issues') {
        setResultsTab('issues');
        setResultsOpen(true);
        return;
      }
      setActiveLayerId(undefined);
      setMapAreaTarget(undefined);
      setPanel(destination);
      if (destination === 'zones') setDraftZones(project?.planting_zones ?? []);
      openRightPanel();
    },
    [
      openRightPanel,
      project?.planting_zones,
      setActiveLayerId,
      setPanel,
      setResultsOpen,
      setResultsTab,
    ],
  );
  const createManualPlan = useStartWorkspace({
    projectId,
    project,
    zones: draftZones,
    refresh: () => refresh({ mapGeometry: true, strict: true }),
    onCommitted: (nextProject) => {
      const nextZones = nextProject.planting_zones ?? [];
      setDraftZones(nextZones);
      setSelectedPatternZoneIds(
        nextZones.flatMap((zone) => (zone.id ? [zone.id] : [])),
      );
      setPanel(null);
      editor.setTool('select');
      openRightPanel();
    },
  });
  const zoneReviewQuery = useQuery(
    workspaceZonePreviewQuery(
      projectId,
      project?.geometry_version,
      pendingZone?.zone,
    ),
  );
  const reviewZones = useMemo(
    () =>
      pendingZone
        ? [
            ...(project?.planting_zones ?? []).filter(
              (zone) => zone.id !== pendingZone.zone.id,
            ),
            pendingZone.zone,
          ]
        : (project?.planting_zones ?? []),
    [pendingZone, project?.planting_zones],
  );
  const zoneCommands = useZoneCommands({
    projectId,
    project,
    refresh: () => refresh({ mapGeometry: true, strict: true }),
    onCommitted: (nextProject, context) => {
      const zones = nextProject.planting_zones ?? [];
      const available = new Set(zones.map((zone) => zone.id));
      const focusId =
        context.kind === 'placement' ? context.zone.id : context.focusId;
      setPendingZone(undefined);
      setZoneReviewOpen(false);
      setDraftZones(zones);
      setSelectedPatternZoneIds((current) => [
        ...new Set([
          ...current.filter((id) => available.has(id)),
          ...(focusId && available.has(focusId) ? [focusId] : []),
        ]),
      ]);
      if (context.kind === 'placement') {
        setMapAreaTarget(undefined);
        setPatternSettingsOpen(true);
        editor.setTool(context.nextTool ?? 'pattern_fill');
        openRightPanel();
      } else {
        setZonePendingDelete(undefined);
        editor.setTool('select');
      }
    },
  });
  const { savePlacementZone, saveManagedZones } = zoneCommands;
  const applyChanges = usePlanChangeCommit({
    projectId,
    stateVersion: project?.state_version,
    preview: changePreview,
    refresh: () => refresh({ strict: true }),
    onCommitted: (result, accepted) => {
      setReviewOpen(false);
      if (
        accepted.id === patternPreview?.change_set?.id ||
        accepted.id === candidateInspection.trial?.id
      ) {
        previewPattern.reset();
        setRowAxis(undefined);
        setRowAxisSource(undefined);
      }
      if (accepted.id === recommendationPreview?.change_set?.id) {
        setRecommendationOpen(false);
        previewRecommendation.reset();
      }
      if (accepted.id === automaticPreview?.change_set?.id) {
        previewAutomatic.reset();
      }
      if (accepted.id === brushPreview?.change_set?.id) {
        setBrushStrokes([]);
        previewBrush.reset();
      }
      setSpeciesAssignmentOpen(false);
      previewChanges.reset();
      editor.setTool('select');
      editor.select([
        ...(result.added_ids ?? []),
        ...(result.updated_ids ?? []),
      ]);
    },
  });
  const deleteObjects = useDeleteSelection({
    projectId,
    project,
    refresh: () => refresh({ strict: true }),
    onCommitted: () => {
      editor.clearSelection();
      setDeleteSelectionOpen(false);
    },
  });
  const historyCommands = usePlanHistoryCommands({
    projectId,
    refresh: () => refresh({ strict: true }),
    onCommitted: () => {
      editor.clearSelection();
      editor.setTool('select');
    },
  });
  const { undoChange, redoChange } = historyCommands;
  const otherBusy =
    createManualPlan.blocked ||
    historyCommands.blocked ||
    zoneCommands.blocked ||
    previewChanges.isPending ||
    previewPattern.isPending ||
    previewRecommendation.isPending ||
    previewAutomatic.isPending ||
    applyChanges.isPending ||
    deleteObjects.blocked ||
    undoChange.isPending ||
    redoChange.isPending;
  const externalEditorBusy =
    projectReadPending ||
    applyChanges.needsRefresh ||
    otherBusy ||
    createRelease.isPending;
  const singlePlacement = useSinglePlacement({
    projectId,
    planVersion: project?.plan?.version,
    geometryVersion: project?.geometry_version ?? 0,
    stateVersion: project?.state_version ?? 0,
    active:
      projectHasPlan &&
      !sourcePreview &&
      !sceneOpen &&
      !comparisonActive &&
      !planLocked &&
      (tool === 'add_tree' || tool === 'add_shrub'),
    kind:
      tool === 'add_tree' ? 'tree' : tool === 'add_shrub' ? 'shrub' : undefined,
    speciesRevisionId:
      tool === 'add_tree' ? singleTreeSpecies : singleShrubSpecies,
    sizeClass: 'standard',
    disabled: externalEditorBusy,
    onAcknowledged: (plan) =>
      acknowledgeProjectWrite(queryClient, projectId, {
        planVersion: plan.version,
      }),
    onApplied: () => refresh({ strict: true }),
    onRefresh: () => refresh({ strict: true }),
  });
  const {
    cursor,
    check: placementCheck,
    hover: handlePointerCoordinate,
  } = singlePlacement;
  const busy = otherBusy || singlePlacement.placing;
  const editorBusy =
    externalEditorBusy ||
    comparisonActive ||
    singlePlacement.placing ||
    singlePlacement.needsRefresh ||
    singlePlacement.refreshing;
  const placementPreview = useMemo(() => {
    if (!cursor || (tool !== 'add_tree' && tool !== 'add_shrub'))
      return undefined;
    return {
      coordinate: cursor,
      radius: placementCheck?.radius ?? (tool === 'add_tree' ? 1.6 : 0.65),
      status: placementCheck?.status ?? 'unknown',
    } as const;
  }, [cursor, placementCheck, tool]);
  const writeOrRecoveryPending =
    applyChanges.isPending ||
    singlePlacement.placing ||
    createManualPlan.blocked ||
    historyCommands.blocked ||
    zoneCommands.blocked ||
    deleteObjects.blocked ||
    applyChanges.needsRefresh ||
    singlePlacement.needsRefresh ||
    singlePlacement.refreshing ||
    createRelease.isPending;
  const manualDraft = manualWorkspaceWork({
    tool,
    hasPlan: projectHasPlan || createManualPlan.writeOutcome === 'confirmed',
    hasPreview: [
      changePreview,
      patternPreview?.change_set,
      recommendationPreview?.change_set,
      brushPreview?.change_set,
    ].some((preview) =>
      Boolean(preview && preview.id !== applyChanges.committedPreviewId),
    ),
    hasPendingZone:
      Boolean(pendingZone) &&
      !(
        zoneCommands.kind === 'placement' &&
        zoneCommands.writeOutcome === 'confirmed'
      ),
    initialZoneDrafts: draftZones.length,
    brushStrokes:
      applyChanges.committedPreviewId &&
      brushPreview?.change_set?.id === applyChanges.committedPreviewId
        ? 0
        : brushStrokes.length,
    brushDrawing,
    hasRowAxis:
      Boolean(rowAxis) &&
      (!applyChanges.committedPreviewId ||
        patternPreview?.change_set?.id !== applyChanges.committedPreviewId),
    rowDrawingPoints,
    placementAreaDrawing,
    zoneDrawing: Boolean(zoneDrawingMode),
    mutationPending: false,
    brushPreviewPending: previewBrush.isPending,
    createPlanPending: false,
    releasePending: false,
  });
  const previewPending =
    previewBrush.isPending ||
    (singlePlacement.placing && singlePlacement.writeOutcome === 'idle');
  const planPersistence = workspacePersistenceState({
    hasDraft: !planLocked && manualDraft.hasDraft,
    previewPending,
    writes: [
      applyChanges,
      singlePlacement,
      zoneCommands,
      createManualPlan,
      deleteObjects,
      historyCommands,
    ].map(({ writeOutcome, readRecovery }) => ({
      outcome: writeOutcome,
      readRecovery,
    })),
  });
  // Release protects navigation too, but it does not save or alter the plan.
  const persistence = workspacePersistenceState({
    hasDraft: planPersistence.hasDraft,
    previewPending,
    writes: [
      {
        outcome: planPersistence.writeOutcome,
        readRecovery: planPersistence.readRecovery,
      },
      {
        outcome: releaseState.writeOutcome,
        readRecovery: releaseState.readRecovery,
      },
    ],
  });
  const savingPlan = planPersistence.writeOutcome === 'writing';
  const navigationPending = persistence.pending;
  const hasUnsavedWork = persistence.hasDraft;
  const planComparison = usePlanComparison({
    mode: editor.planComparison,
    setMode: setPlanComparison,
    available: projectHasPlan && Boolean(project?.map_ready) && !sceneOpen,
    blocked:
      projectReadPending ||
      writeOrRecoveryPending ||
      busy ||
      brushDrawing ||
      placementAreaDrawing ||
      Boolean(zoneDrawingMode) ||
      rowDrawingPoints > 0,
  });
  const navigationBlocker = useWorkspaceNavigation({
    projectId,
    blocksLeaving: persistence.blocksLeaving,
    hasUnsavedWork,
    writeOutcome: persistence.writeOutcome,
    readRecovery: persistence.readRecovery,
    editingBlocked: persistence.editingBlocked || projectReadPending,
  });

  const reloadAfterConflict = useCallback(async () => {
    // Keep the draft and the conflict visible until the current project is read.
    await refresh({ mapGeometry: true, strict: true });
    createManualPlan.reset();
    savePlacementZone.reset();
    saveManagedZones.reset();
    singlePlacement.clear();
    resetPlacementPreviews();
    applyChanges.reset();
    deleteObjects.reset();
    undoChange.reset();
    redoChange.reset();
    createRelease.reset();
    mapEditInFlightRef.current = false;

    setRowAxis(undefined);
    setRowAxisSource(undefined);
    discardZoneDrawing();
    editor.reset();
  }, [
    singlePlacement,
    applyChanges,
    createManualPlan,
    createRelease,
    deleteObjects,
    discardZoneDrawing,
    editor,
    redoChange,
    refresh,
    setRowAxis,
    setRowAxisSource,
    resetPlacementPreviews,
    saveManagedZones,
    savePlacementZone,
    undoChange,
  ]);
  const activateTool = useCallback(
    (nextTool: MapTool) => {
      if (editorBusy) return;
      // The hand is a view mode, not a replacement planting operation.
      if (nextTool === 'pan') {
        setMapPanActive((value) => !value);
        return;
      }
      setMapPanActive(false);
      if (nextTool === 'pattern_fill') setPatternSettingsOpen(true);

      if (nextTool === tool) {
        setActiveLayerId(undefined);
        setPanel(null);
        setSpeciesAssignmentOpen(false);
        openRightPanel();
        setIdeRightTab(ideTabForTool(nextTool));
        return;
      }
      if (changePreview || brushStrokes.length || rowAxis) {
        setPendingTool(nextTool);
        return;
      }
      if (
        (planLocked || sourcePreview) &&
        [
          'add_tree',
          'add_shrub',
          'pattern_row',
          'pattern_fill',
          'brush',
          'move',
          'copy',
          'draw_area',
        ].includes(nextTool)
      )
        return;
      if (
        !projectHasPlan &&
        [
          'add_tree',
          'add_shrub',
          'pattern_row',
          'pattern_fill',
          'brush',
          'move',
          'copy',
          'select_box',
          'select_lasso',
        ].includes(nextTool)
      )
        return;
      if (zoneDrawingSession && nextTool !== 'draw_area') {
        discardZoneDrawing();
        setPanel(null);
      }
      resetPlacementPreviews();
      if (nextTool === 'pattern_row') setRowInputMode('pick');
      if (nextTool !== 'pattern_row') {
        setRowAxis(undefined);
        setRowAxisSource(undefined);
      }
      if (
        nextTool === 'pattern_row' ||
        nextTool === 'pattern_fill' ||
        nextTool === 'brush'
      ) {
        editor.clearSelection();
        setActiveLayerId(undefined);
        setPanel(null);
        openRightPanel();
      }
      if (
        nextTool === 'pattern_fill' ||
        nextTool === 'pattern_row' ||
        nextTool === 'brush'
      ) {
        setSelectedPatternZoneIds((current) =>
          placementZoneSelection(current, plantingZoneIds),
        );
      }
      editor.setTool(
        tool === nextTool && nextTool !== 'select' ? 'select' : nextTool,
      );
    },
    [
      editorBusy,
      zoneDrawingSession,
      discardZoneDrawing,
      tool,
      changePreview,
      brushStrokes.length,
      rowAxis,
      planLocked,
      sourcePreview,
      projectHasPlan,
      setPatternSettingsOpen,
      setRowAxis,
      setRowAxisSource,
      setRowInputMode,
      setSelectedPatternZoneIds,
      setSpeciesAssignmentOpen,
      resetPlacementPreviews,
      editor,
      setActiveLayerId,
      setPanel,
      openRightPanel,
      setIdeRightTab,
      plantingZoneIds,
    ],
  );
  const {
    addMapArea,
    handleMapArea,
    handleMapStackSelect,
    selectFromExplorer,
  } = useWorkspaceSelection({
    projectId,
    projectHasPlan,
    zoneCount: project?.planting_zones?.length ?? 0,
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
    clearSelection: editor.clearSelection,
    select: editor.select,
    selectionBlocked: hasUnsavedWork || editorBusy,
    activateTool,
    setIdeRightTab,
    sceneOpen,
    sceneReview,
    mapViewport,
  });
  const previewSelectionTransform = (
    mode: 'move' | 'copy',
    coordinate: [number, number],
  ) => {
    const plan = project?.plan;
    if (!plan || !selectedObjects.length) return;
    const copiedGroupId =
      mode === 'copy' ? `group-${globalThis.crypto.randomUUID()}` : undefined;
    const draft = groupTransformDraft(
      plan.version,
      selectedObjects,
      mode,
      coordinate,
      copiedGroupId,
    );
    if (draft) previewChanges.mutate(draft);
  };

  const previewSelectionLock = (locked: boolean) => {
    const plan = project?.plan;
    if (!plan || !selectedObjects.length) return;
    previewChanges.mutate({
      base_plan_version: plan.version,
      source: 'group',
      label: locked
        ? `Закрепление объектов (${selectedObjects.length})`
        : `Снятие закрепления (${selectedObjects.length})`,
      policy: 'all_or_nothing',
      operations: selectedObjects.flatMap((object) =>
        object.id
          ? [
              {
                type: 'update' as const,
                object_id: object.id,
                changes: { locked },
              },
            ]
          : [],
      ),
    });
  };
  const previewSpeciesAssignment = (
    revisionId: string,
    sizeClass: 'sapling' | 'standard' | 'large',
  ) => {
    const plan = project?.plan;
    if (!plan || !selectedObjects.length) return;
    previewChanges.mutate({
      base_plan_version: plan.version,
      source: selectedObjects.length > 1 ? 'group' : 'manual',
      label:
        selectedObjects.length > 1
          ? `Порода для группы (${selectedObjects.length})`
          : 'Назначение породы',
      policy: 'all_or_nothing',
      operations: selectedObjects.flatMap((object) =>
        object.id
          ? [
              {
                type: 'update' as const,
                object_id: object.id,
                changes: {
                  species_revision_id: revisionId,
                  size_class: sizeClass,
                },
              },
            ]
          : [],
      ),
    });
  };
  const handleCoordinate = (coordinate: [number, number]) => {
    if (editorBusy || planLocked || !projectHasPlan) return;
    if (tool === 'add_tree' || tool === 'add_shrub') {
      setRightOpen(true);
      setIdeRightTab('tool');
      void singlePlacement.place(coordinate);
    }
    if (
      (tool === 'move' || tool === 'copy' || tool === 'select') &&
      selectedObjects.length &&
      !mapEditInFlightRef.current
    ) {
      clearMoveValidation();
      mapEditInFlightRef.current = true;
      previewSelectionTransform(tool === 'copy' ? 'copy' : 'move', coordinate);
      mapEditInFlightRef.current = false;
    }
  };
  useWorkspaceKeyboard({
    onSelectAll:
      !hasUnsavedWork && !editorBusy && !planComparison.active && projectHasPlan
        ? () =>
            selectFromExplorer(
              planObjects.flatMap((object) => (object.id ? [object.id] : [])),
              false,
            )
        : undefined,
    blocked: Boolean(
      writeOrRecoveryPending ||
      releaseOpen ||
      deleteSelectionOpen ||
      zonePendingDelete ||
      pendingTool ||
      navigationBlocker.state === 'blocked',
    ),
    onEscape: () => {
      if (planComparison.active) {
        planComparison.change('editing');
        return;
      }
      if (zoneDrawingSession) {
        cancelZoneDrawing();
        return;
      }
      if (mapInspectTarget) {
        setMapInspectTarget(undefined);
        setMapHoverTarget(undefined);
        return;
      }
      if (selectedIds.length && tool === 'select' && !changePreview) {
        editor.clearSelection();
        return;
      }
      if (sceneOpen) {
        changeMapMode('2d');
        return;
      }
      const hasActiveOperation =
        previewChanges.isPending ||
        tool !== 'select' ||
        Boolean(changePreview) ||
        recommendationOpen ||
        Boolean(recommendationPreview) ||
        Boolean(automaticPreview) ||
        Boolean(patternPreview) ||
        brushStrokes.length > 0 ||
        speciesAssignmentOpen;
      if (!hasActiveOperation) {
        if (selectedIds.length) editor.clearSelection();
        return;
      }
      resetPlacementPreviews();
      setRowAxis(undefined);
      setRowAxisSource(undefined);
      editor.setTool('select');
    },
    onReview:
      changePreview?.can_apply && !editorBusy
        ? () => setReviewOpen(true)
        : undefined,
    onDelete:
      selectedIds.length > 0 &&
      !editorBusy &&
      !selectedLocked &&
      !hasUnsavedWork
        ? () => setDeleteSelectionOpen(true)
        : undefined,
    onUndo:
      !planLocked &&
      !editorBusy &&
      historyQuery.data?.can_undo &&
      !undoChange.isPending
        ? () => undoChange.mutate()
        : undefined,
    onRedo:
      !planLocked &&
      !editorBusy &&
      historyQuery.data?.can_redo &&
      !redoChange.isPending
        ? () => redoChange.mutate()
        : undefined,
  });
  const operationError = [
    createManualPlan,
    savePlacementZone,
    saveManagedZones,
    previewChanges,
    previewPattern,
    previewRecommendation,
    previewAutomatic,
    previewBrush,
    applyChanges,
    deleteObjects,
    undoChange,
    redoChange,
  ]
    .filter(
      (mutation) =>
        mutation.error &&
        !(
          mutation.error instanceof Error &&
          mutation.error.name === 'AbortError'
        ),
    )
    .sort((a, b) => b.submittedAt - a.submittedAt)[0]?.error;
  const showMapStatus = Boolean(
    (placementCheck && (tool === 'add_tree' || tool === 'add_shrub')) ||
    (moveLiveCheck && (tool === 'move' || tool === 'select')) ||
    mapGeometryQuery.isFetching ||
    mapGeometryMetadata?.truncated,
  );
  const mapInfoTarget = mapInspectTarget ?? mapHoverTarget;
  const leaveWorkspace = (path: string) => {
    navigate(path);
  };
  const inspectorView = resolveInspectorView({
    recommendationOpen,
    panel,
    hasPlan: projectHasPlan,
    sourcePreview,
    hasLayer: Boolean(activeLayer),
    tool,
    drawingPlacementArea: placementAreaDrawing,
    hasPattern: Boolean(patternPreview),
    hasChange: Boolean(changePreview),
    hasRecommendation: Boolean(recommendationPreview || automaticPreview),
    assigningSpecies: speciesAssignmentOpen,
    selectionCount: selectedIds.length,
    hasArea: Boolean(mapAreaTarget),
  });
  const toolNames = WORKSPACE_TOOL_NAMES;
  const activeToolHint = workspaceToolHint({
    tool,
    selectedZoneCount: selectedPatternZoneIds.length,
    rowAcceptedCount: patternPreview?.accepted_count,
    rowInputMode,
    hasRowAxis: Boolean(rowAxis),
  });
  return {
    ...presentation,
    ...placement,
    activateTool,
    activeLayerId,
    activeToolHint,
    addMapArea,
    applyChanges,
    busy,
    changeIdeRightTab,
    changeMapMode,
    closeRightPanel,
    createManualPlan,
    createRelease,
    deleteObjects,
    deleteSelectionOpen,
    dismissedOperationError,
    draftZones,
    editor,
    editorBusy,
    externalEditorBusy,
    focusZones,
    growthHorizon,
    growthScenario: {
      ...forecast,
      open: forecastActive,
      toggle: () => {
        if (growthScenarioOpen) updateGrowthHorizon(0);
        setGrowthScenarioOpen(!growthScenarioOpen);
      },
      close: () => {
        updateGrowthHorizon(0);
        setGrowthScenarioOpen(false);
      },
      focus: (issue: import('@green/api-client').ValidationIssue) => {
        const ids = issue.related_object_ids?.length
          ? issue.related_object_ids
          : issue.object_id
            ? [issue.object_id]
            : [];
        selectFromExplorer(ids, true);
      },
    },
    mapScenarioIssues,
    mapScenarioObjects,
    handleCoordinate,
    handleMapArea,
    handleMapExtent,
    handleMapStackSelect,
    handlePointerCoordinate,
    hasUnsavedWork,
    historyCommands,
    historyQuery,
    ideRightTab,
    inspectorView,
    leaveWorkspace,
    leftOpen,
    libraryOpen,
    mapAreaTarget,
    mapGeometryMetadata: cad.vectorGeometryEnabled
      ? mapGeometryMetadata
      : undefined,
    cadBaseReady: cad.baseReady,
    mapLoading: cad.pending || mapGeometryPending || geometryDrawing,
    mapLoadingLabel:
      cad.pending || !mapGeometryDelivery
        ? 'Открываем чертёж'
        : mapGeometryPending
          ? 'Обновляем карту'
          : 'Отрисовка карты',
    onGeometryRenderState,
    mapGeometryQuery,
    mapGeometryDelivery,
    cadSource: cad.source,
    cadDisplayWarning: cad.displayWarning,
    onCadRenderState: cad.onRenderState,
    mapInfoTarget,
    mapInspectTarget,
    mapPanActive,
    mapRenderMode,
    mapViewport,
    moveLiveCheck,
    navigate,
    navigateWorkspace,
    navigationBlocker,
    navigationPending,
    openLeftPanel: editor.openSourceLayers,
    openRightPanel,
    operationError,
    panel,
    candidateInspection,
    pendingScene,
    pendingTool,
    pendingZone,
    persistence,
    preparationIssue,
    placementAreaDrawing,
    placementCheck,
    placementMasksQuery,
    placementPreview,
    planComparison,
    previewSelectionLock,
    previewSelectionMoveLive,
    previewSpeciesAssignment,
    project,
    projectId,
    projectQuery,
    redoChange,
    release,
    releaseOpen,
    releaseState,
    reloadAfterConflict,
    resultsOpen,
    resultsTab,
    reviewOpen,
    reviewZones,
    rightOpen,
    saveManagedZones,
    savePlacementZone,
    savingPlan,
    planPersistence,
    sceneHorizon,
    sceneInitialViewState,
    sceneMounted,
    sceneOpen,
    sceneQuery,
    sceneRequestHorizon,
    sceneReview,
    sceneViewStateRef,
    selectFromExplorer,
    selectPatternZones,
    selectedIds,
    setActiveLayerId,
    setDeleteSelectionOpen,
    setDismissedOperationError,
    setDraftZones,
    setGrowthHorizon,
    setIdeRightTab,
    setLeftOpen,
    setLibraryOpen,
    setMapAreaTarget,
    setMapHoverTarget,
    setMapInspectTarget,
    setMapPanActive,
    setMapRenderMode,
    setPanel,
    setPendingScene,
    setPendingTool,
    setPendingZone,
    beginPlacementZoneDrawing,
    setReleaseOpen,
    setResultsOpen,
    setResultsTab,
    setReviewOpen,
    setRightOpen,
    setSceneHorizon,
    setVisibility,
    beginZoneDrawing,
    redrawZone,
    cancelZoneReview,
    finishZoneDrawing,
    cancelZoneDrawing,
    discardZoneDrawing,
    setZonePendingDelete,
    setZoneReviewOpen,
    showMapStatus,
    singlePlacement,
    sourceImportStatus: project?.import_status,
    speciesNames,
    speciesQuery,
    speciesShortlistQuery,
    tool,
    toolNames,
    undoChange,
    visibility,
    zoneCommands,
    zoneDrawingMode,
    zoneDrawingSession,
    zonePendingDelete,
    zoneReviewOpen,
    zoneReviewQuery,
    zoneSpeciesShortlistQuery,
  };
}
export type WorkspaceModel = ReturnType<typeof useWorkspaceModel>;
export type WorkspaceReadyModel = Omit<WorkspaceModel, 'project'> & {
  project: import('@green/api-client').Project;
};
