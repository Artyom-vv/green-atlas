import { useAssistantMapBridge } from './useAssistantMapBridge';
import { useWorkspaceCadSource } from './useWorkspaceCadSource';
import { useWorkspaceRefresh } from '@/features/workspace/api/useWorkspaceRefresh';
import { useWorkspaceNavigation } from './useWorkspaceNavigation';
import { useMoveValidation } from '@/features/plan-changes/model/useMoveValidation';
import { previewPlanChanges } from '@/features/plan-changes/api/previewPlanChanges';
import {
  workspaceProjectQuery,
  workspaceHistoryQuery,
  workspaceSpeciesQuery,
  workspaceSceneQuery,
  workspaceZonePreviewQuery,
} from '@/features/workspace/api/workspaceQueries';
import {
  buildingTargetsQuery,
  selectionSpeciesQuery,
  zoneSpeciesQuery,
  placementMasksQueryOptions,
} from '@/features/workspace/api/placementQueries';
import {
  previewWorkspacePattern,
  previewWorkspaceBrush,
  previewWorkspaceRecommendation,
} from '@/features/workspace/api/workspacePreviews';
import type {
  EditorPanel as WorkspacePanel,
  EditorRightTab as IdeRightTab,
} from '@/entities/editor';
import { useEditorSession } from '@/entities/editor';
import type { PlanViewState } from '@/entities/editor/model/planViewState';
import { type GrowthHorizon } from '@/entities/planting-forecast';
import { zoneChangeFrame } from '@/entities/planting-zone/model/zoneChangeSummary';
import { groupTransformDraft } from '@/entities/planting/model/groupTransform';
import { placementPreviewFrame } from '@/entities/planting/model/placementPreviewFrame';
import { metadataOnlyObjectIds } from '@/entities/planting/model/planPresentation';
import { useProjectAssistant } from '@/features/assistant';
import {
  toRowSketchSettings,
  usePatternForm,
  useSinglePlacement,
  type PatternFormValues,
} from '@/features/placement';
import { toLiveBrushSettings } from '@/features/placement/model/brushForm';
import { useBrushForm } from '@/features/placement/model/useBrushForm';
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
import { useRecommendationForm } from '@/features/recommendation';
import { useStartWorkspace } from '@/features/start-workspace/model/useStartWorkspace';
import { resolveInspectorView } from '@/features/workspace/inspectorView';
import { manualWorkspaceWork } from '@/features/workspace/manualWorkspaceWork';
import {
  placementZoneSelection,
  zoneCollection,
  zoneExtent,
} from '@/features/workspace/zoneSelection';
import { useLatestPreview } from '@/shared/async/useLatestPreview';
import { featureAvailability } from '@/shared/config/featureAvailability';
import { initialWorkspaceExtent } from './initialWorkspaceExtent';
import { useViewportGeometry } from '@/widgets/map/api/useViewportGeometry';
import {
  type MapAreaTarget,
  type MapHoverTarget,
  type MapViewportHandle,
} from '@/widgets/map/model/mapContracts';
import { type MapTool } from '@/widgets/map/ui/MapToolbar';
import type { SceneReviewHandle } from '@/widgets/scene/ui/SceneReview';
import type { BuildingScreenRequest } from '@green/api-client';
import {
  type BrushPreview,
  type BrushPreviewRequest,
  type BrushStroke,
  type ChangeSetPreview,
  type Layer,
  type PatternPreview,
  type PatternPreviewRequest,
  type PlanChangeSetDraft,
  type PlanObject,
  type PlantingZoneAssignment,
  type RecommendationPreview,
  type RecommendationRequest,
} from '@green/api-client';
import { useQuery } from '@tanstack/react-query';
import { sourceIdentity } from '@/features/source-preparation/model/sourcePreparation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useWatch } from 'react-hook-form';
import { useNavigate } from 'react-router-dom';
import { ideTabForTool } from './workspacePresentation';
import { useWorkspaceKeyboard } from './useWorkspaceKeyboard';
import { useWorkspaceSelection } from './useWorkspaceSelection';
import {
  WORKSPACE_TOOL_NAMES,
  workspaceToolHint,
} from './workspaceToolPresentation';
type WorkspaceDestination = Extract<
  WorkspacePanel,
  'zones' | 'plantings' | 'issues'
>;
const EMPTY_PLAN_OBJECTS: PlanObject[] = [];
const EMPTY_LAYERS: Layer[] = [];
export function useWorkspaceModel(projectId: string) {
  const navigate = useNavigate();

  const projectQuery = useQuery(workspaceProjectQuery(projectId));
  const project = projectQuery.data;
  const cad = useWorkspaceCadSource(project);
  const mapViewport = useRef<MapViewportHandle>(null);
  const sceneReview = useRef<SceneReviewHandle>(null);
  const sceneViewStateRef = useRef<PlanViewState | undefined>(undefined);

  const mapEditInFlightRef = useRef(false);
  const initializedProjectRef = useRef<string | undefined>(undefined);
  const [pendingTool, setPendingTool] = useState<MapTool>();
  const [deleteSelectionOpen, setDeleteSelectionOpen] = useState(false);
  const assistant = useProjectAssistant();
  const setAssistantOpen = assistant.setOpen;
  const stopAssistant = assistant.stop;
  const assistantPending = assistant.pending;
  const autonomousPreview =
    assistant.assistantMode === 'autonomous'
      ? assistant.autonomousPreview
      : undefined;
  const assistantZonePreview =
    autonomousPreview?.kind === 'planting_zones'
      ? autonomousPreview.preview
      : undefined;
  const assistantPreview =
    assistant.assistantMode === 'autonomous'
      ? autonomousPreview?.kind === 'planting_zones'
        ? undefined
        : autonomousPreview?.preview
      : assistant.proposal?.stale
        ? undefined
        : assistant.proposal?.preview;
  const editor = useEditorSession((state) => state);
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
  const changeIdeRightTab = (next: IdeRightTab) => {
    const availableTab =
      next === 'assistant' && !featureAvailability.assistant
        ? ideTabForTool(tool, placementOwnsContext)
        : next;
    setIdeRightTab(availableTab);
    setRightOpen(true);
    assistant.setOpen(availableTab === 'assistant');
  };
  const [mapHoverTarget, setMapHoverTarget] = useState<MapHoverTarget>();
  const [mapInspectTarget, setMapInspectTarget] = useState<MapHoverTarget>();
  const [mapAreaTarget, setMapAreaTarget] = useState<MapAreaTarget>();

  const [draftZones, setDraftZones] = useState<PlantingZoneAssignment[]>([]);
  const [zonePendingDelete, setZonePendingDelete] =
    useState<PlantingZoneAssignment>();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [rowAxis, setRowAxis] = useState<{
    type: 'LineString';
    coordinates: number[][];
  }>();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [dismissedOperationError, setDismissedOperationError] =
    useState<unknown>();
  const [rowInputMode, setRowInputMode] = useState<'pick' | 'draw' | 'ready'>(
    'pick',
  );
  const [rowDrawingPoints, setRowDrawingPoints] = useState(0);
  const rowForm = usePatternForm();
  const fillForm = usePatternForm();
  const recommendationForm = useRecommendationForm();
  const recommendationValues = useWatch({
    control: recommendationForm.control,
  });
  const rowValues = useWatch({
    control: rowForm.control,
    compute: (values: PatternFormValues) => values,
  });
  const fillValues = useWatch({
    control: fillForm.control,
    compute: (values: PatternFormValues) => values,
  });
  const patternForm = tool === 'pattern_row' ? rowForm : fillForm;
  const patternValues = tool === 'pattern_row' ? rowValues : fillValues;
  const rowSettings = toRowSketchSettings(rowValues);
  const [rowAxisSource, setRowAxisSource] = useState<{
    type: 'dxf' | 'manual';
    label: string;
  }>();
  const [selectedPatternZoneIds, setSelectedPatternZoneIds] = useState<
    string[]
  >([]);
  const [recommendationOpen, setRecommendationOpen] = useState(false);
  const [buildingScreenActive, setBuildingScreenActive] = useState(false);
  const [brushStrokes, setBrushStrokes] = useState<BrushStroke[]>([]);
  const [brushWidth, setBrushWidth] = useState(12);
  const [brushOperation, setBrushOperation] = useState<'add' | 'subtract'>(
    'add',
  );
  const brushForm = useBrushForm();
  const brushSettings = useWatch({
    control: brushForm.control,
    compute: toLiveBrushSettings,
  });
  const [brushDrawing, setBrushDrawing] = useState(false);
  const [speciesAssignmentOpen, setSpeciesAssignmentOpen] = useState(false);
  const [speciesCatalogBrowsing, setSpeciesCatalogBrowsing] = useState(true);
  const [pendingZone, setPendingZone] = useState<ZoneReviewDraft>();
  const [zoneReviewOpen, setZoneReviewOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [patternSettingsOpen, setPatternSettingsOpen] = useState(false);
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
    [setMapTool, setPanel, setRightOpen],
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
  const [singleTreeSpecies, setSingleTreeSpecies] = useState<string>();
  const [singleShrubSpecies, setSingleShrubSpecies] = useState<string>();
  const projectBasis = [
    projectId,
    project?.plan?.version,
    project?.geometry_version,
    project?.state_version,
  ];
  const previewPattern = useLatestPreview<
    PatternPreviewRequest,
    PatternPreview
  >({
    scopeKey: JSON.stringify([
      ...projectBasis,
      tool,
      patternValues,
      rowAxis,
      selectedPatternZoneIds,
    ]),
    execute: (request, signal) =>
      previewWorkspacePattern(projectId, request, signal),
    onAccepted: (result) => {
      setPatternSettingsOpen(true);
      const frame = placementPreviewFrame(result.change_set?.additions ?? []);
      if (frame) mapViewport.current?.fitGeometry(frame);
    },
  });
  const previewRecommendation = useLatestPreview<
    RecommendationRequest | BuildingScreenRequest,
    RecommendationPreview
  >({
    scopeKey: JSON.stringify([
      ...projectBasis,
      selectedPatternZoneIds,
      recommendationValues,
    ]),
    execute: (request, signal) =>
      previewWorkspaceRecommendation(projectId, request, signal),
    onAccepted: (result) => {
      setPatternSettingsOpen(true);
      const frame = placementPreviewFrame(result.change_set?.additions ?? []);
      if (frame) mapViewport.current?.fitGeometry(frame);
      setActiveLayerId(undefined);
      setPanel(null);
      setRightOpen(true);
    },
  });
  const previewBrush = useLatestPreview<BrushPreviewRequest, BrushPreview>({
    scopeKey: JSON.stringify([
      ...projectBasis,
      tool,
      selectedPatternZoneIds,
      brushSettings,
      brushWidth,
      brushOperation,
      brushStrokes,
    ]),
    execute: (request, signal) =>
      previewWorkspaceBrush(projectId, request, signal),
  });
  const previewChanges = useLatestPreview<PlanChangeSetDraft, ChangeSetPreview>(
    {
      scopeKey: JSON.stringify(projectBasis),
      execute: (draft, signal) => previewPlanChanges(projectId, draft, signal),
      onAccepted: () => {
        setReviewOpen(true);
        editor.setTool('select');
        setActiveLayerId(undefined);
        setPanel(null);
        setRightOpen(true);
      },
    },
  );
  const manualPreview = previewChanges.data;
  const previewDraft = manualPreview
    ? { previewId: manualPreview.id, draft: previewChanges.variables }
    : undefined;
  const invalidatePattern = previewPattern.invalidate;
  const subscribePattern = patternForm.subscribe;
  useEffect(
    () =>
      subscribePattern({
        formState: { values: true },
        callback: invalidatePattern,
      }),
    [subscribePattern, invalidatePattern],
  );
  const invalidateBrush = previewBrush.invalidate;
  const invalidateRecommendation = previewRecommendation.invalidate;
  const subscribeRecommendation = recommendationForm.subscribe;
  useEffect(
    () =>
      subscribeRecommendation({
        formState: { values: true },
        callback: invalidateRecommendation,
      }),
    [subscribeRecommendation, invalidateRecommendation],
  );
  const subscribeBrush = brushForm.subscribe;
  useEffect(
    () =>
      subscribeBrush({
        formState: { values: true },
        callback: invalidateBrush,
      }),
    [subscribeBrush, invalidateBrush],
  );
  const patternPreview = previewPattern.data;
  const recommendationPreview = previewRecommendation.data;
  const brushPreview = previewBrush.data;
  const changePreview =
    patternPreview?.change_set ??
    recommendationPreview?.change_set ??
    brushPreview?.change_set ??
    manualPreview;
  const growthHorizon = assistant.horizon ?? 0;
  const updateAssistantHorizon = assistant.setHorizon;
  const setGrowthHorizon = useCallback(
    (value: GrowthHorizon) => updateAssistantHorizon(value ?? 0),
    [updateAssistantHorizon],
  );
  const syncAssistantSelection = assistant.setSelectedIds;
  useEffect(() => {
    syncAssistantSelection(selectedIds);
  }, [selectedIds, syncAssistantSelection]);
  const syncAssistantZones = assistant.setSelectedZoneIds;
  useEffect(() => {
    syncAssistantZones(selectedPatternZoneIds);
  }, [selectedPatternZoneIds, syncAssistantZones]);
  useEffect(() => {
    if (!assistantPreview) return;
    setSceneOpen(false);
    const frame = placementPreviewFrame([
      ...(assistantPreview.additions ?? []),
      ...(assistantPreview.updates ?? []),
    ]);
    if (frame) mapViewport.current?.fitGeometry(frame);
    else if (assistantPreview.deletion_ids?.length)
      mapViewport.current?.fitObjects(assistantPreview.deletion_ids);
  }, [assistantPreview]);
  useEffect(() => {
    if (!assistantZonePreview) return;
    setSceneOpen(false);
    const frame = zoneChangeFrame(assistantZonePreview);
    if (frame) mapViewport.current?.fitGeometry(frame);
  }, [assistantZonePreview]);
  const [sceneOpen, setSceneOpen] = useState(false);
  const [pendingScene, setPendingScene] = useState(false);
  const [mapRenderMode, setMapRenderMode] = useState<'design' | 'cad'>(
    'design',
  );
  const [mapPanActive, setMapPanActive] = useState(false);
  const [sceneMounted, setSceneMounted] = useState(false);
  const sceneHorizon = growthHorizon ?? 0;
  const setSceneHorizon = setGrowthHorizon;
  const [sceneRequestHorizon, setSceneRequestHorizon] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(
      () => setSceneRequestHorizon(sceneHorizon),
      160,
    );
    return () => window.clearTimeout(timer);
  }, [sceneHorizon]);
  const [sceneInitialViewState, setSceneInitialViewState] =
    useState<PlanViewState>();
  const changeMapMode = useCallback(
    (mode: '2d' | '3d') => {
      if (mode === '3d') {
        discardZoneDrawing();
        setMapTool('select');
        setPanel(null);
        setRecommendationOpen(false);
        setActiveLayerId(undefined);
        setMapPanActive(false);
        const state = mapViewport.current?.getViewState();
        if (state) {
          sceneViewStateRef.current = state;
          setSceneInitialViewState(state);
        }
        setSceneMounted(true);
        setSceneOpen(true);
        return;
      }
      const state =
        sceneReview.current?.getViewState() ?? sceneViewStateRef.current;
      if (state) {
        // Keep the latest 3D camera authoritative for both the map we are
        // returning to and the next 3D activation. Previously only a ref was
        // updated, so reopening 3D re-imported the stale entry camera.
        sceneViewStateRef.current = state;
        setSceneInitialViewState(state);
        mapViewport.current?.applyViewState(state);
      }
      setSceneOpen(false);
    },
    [discardZoneDrawing, setActiveLayerId, setMapTool, setPanel],
  );
  useEffect(() => setMapInspectTarget(undefined), [tool]);
  const releaseState = useProjectRelease(projectId, {
    plan: project?.plan ?? undefined,
    geometryVersion: project?.geometry_version,
    initialHorizon: growthHorizon,
  });
  const { release, createRelease } = releaseState;
  const layers = project?.layers ?? EMPTY_LAYERS;
  const planObjects = project?.plan?.objects ?? EMPTY_PLAN_OBJECTS;
  const sourceWarnings = project?.source_file?.warnings ?? [];
  const projectHasPlan = Boolean(project?.plan);
  useEffect(() => {
    const request = assistant.focusRequest;
    if (!request?.objectIds.length) return;
    const known = new Set(planObjects.map((object) => object.id));
    // Wait for the refreshed plan source. Fitting earlier would silently do
    // nothing because the newly added features are not installed in the map.
    if (!request.objectIds.every((id) => known.has(id))) return;
    mapViewport.current?.fitObjects(request.objectIds);
  }, [assistant.focusRequest, planObjects]);
  const sourcePreview = Boolean(
    project &&
    (!project.map_ready || project.import_status?.editability === 'read_only'),
  );
  const planLocked = project?.import_status?.editability === 'read_only';
  const historyQuery = useQuery(
    workspaceHistoryQuery(projectId, projectHasPlan && !planLocked),
  );
  const speciesQuery = useQuery(workspaceSpeciesQuery());
  const {
    query: mapGeometryQuery,
    delivery: mapGeometryDelivery,
    metadata: mapGeometryMetadata,
    onExtentChange: handleMapExtent,
  } = useViewportGeometry({
    projectId,
    geometryVersion: project?.geometry_version,
    sourceKey: sourceIdentity(project),
    enabled: cad.vectorGeometryEnabled,
  });
  useAssistantMapBridge({
    projectId,
    project,
    sceneOpen,
    mapViewportRef: mapViewport,
    registerMapControl: assistant.registerMapControl,
  });
  const sceneQuery = useQuery(
    workspaceSceneQuery(projectId, project, sceneRequestHorizon, sceneOpen),
  );
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
  }, [project, setPanel]);

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
  const buildingTargets = useQuery(
    buildingTargetsQuery(
      projectId,
      selectedPatternZoneIds,
      project?.state_version,
      recommendationOpen &&
        buildingScreenActive &&
        selectedPatternZoneIds.length > 0,
    ),
  );
  const screenGeometry =
    recommendationOpen && buildingScreenActive
      ? buildingTargets.data?.geometry
      : undefined;
  const focusGeometry = useMemo(
    () => screenGeometry ?? zoneCollection(selectedPatternZones),
    [screenGeometry, selectedPatternZones],
  );
  useEffect(() => {
    if (screenGeometry && !recommendationPreview)
      mapViewport.current?.fitGeometry(screenGeometry);
  }, [screenGeometry, recommendationPreview]);
  useEffect(() => {
    if (!recommendationOpen) setBuildingScreenActive(false);
  }, [recommendationOpen]);
  const brushZones = useMemo(
    () =>
      (project?.planting_zones ?? []).filter(
        (zone) => zone.id && selectedPatternZoneIds.includes(zone.id),
      ),
    [project?.planting_zones, selectedPatternZoneIds],
  );
  const plantingZoneIds = useMemo(
    () =>
      (project?.planting_zones ?? []).flatMap((zone) =>
        zone.id ? [zone.id] : [],
      ),
    [project?.planting_zones],
  );
  const selectPatternZones = useCallback((ids: string[]) => {
    setSelectedPatternZoneIds([...new Set(ids)]);
    setMapAreaTarget(undefined);
  }, []);
  const focusZones = (zones: PlantingZoneAssignment[]) => {
    const extent = zoneExtent(zones);
    if (sceneOpen && extent) sceneReview.current?.fitExtent(extent);
    else if (zones.length)
      mapViewport.current?.fitGeometry(zoneCollection(zones));
  };
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
  const selectedLocked = selectedObjects.some((object) => object.locked);

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
  const issues = project?.plan?.issues ?? [];
  const metadataOnlyIds = useMemo(
    () => metadataOnlyObjectIds(project?.plan?.issues ?? []),
    [project?.plan?.issues],
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
    preview: changePreview,
    refresh: () => refresh({ strict: true }),
    onCommitted: (result, accepted) => {
      setReviewOpen(false);
      if (accepted.id === patternPreview?.change_set?.id) {
        previewPattern.reset();
        setRowAxis(undefined);
        setRowAxisSource(undefined);
      }
      if (accepted.id === recommendationPreview?.change_set?.id) {
        setRecommendationOpen(false);
        previewRecommendation.reset();
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
    applyChanges.isPending ||
    deleteObjects.blocked ||
    undoChange.isPending ||
    redoChange.isPending;
  const externalEditorBusy =
    applyChanges.needsRefresh ||
    otherBusy ||
    createRelease.isPending ||
    Boolean(assistantPreview || assistantZonePreview) ||
    Boolean(assistant.proposal) ||
    assistant.pending === 'preparing' ||
    assistant.pending === 'applying' ||
    assistant.pending === 'undoing';
  const singlePlacement = useSinglePlacement({
    projectId,
    planVersion: project?.plan?.version,
    geometryVersion: project?.geometry_version ?? 0,
    stateVersion: project?.state_version ?? 0,
    active:
      projectHasPlan &&
      !sourcePreview &&
      !sceneOpen &&
      !planLocked &&
      (tool === 'add_tree' || tool === 'add_shrub'),
    kind:
      tool === 'add_tree' ? 'tree' : tool === 'add_shrub' ? 'shrub' : undefined,
    speciesRevisionId:
      tool === 'add_tree' ? singleTreeSpecies : singleShrubSpecies,
    sizeClass: 'standard',
    disabled: externalEditorBusy,
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
  const savingPlan =
    applyChanges.isPending ||
    singlePlacement.placing ||
    saveManagedZones.isPending ||
    savePlacementZone.isPending ||
    createManualPlan.isPending ||
    deleteObjects.isPending ||
    undoChange.isPending ||
    redoChange.isPending;
  const assistantWritePending =
    assistant.pending === 'applying' || assistant.pending === 'undoing';
  const writeOrRecoveryPending =
    savingPlan ||
    createManualPlan.blocked ||
    historyCommands.blocked ||
    zoneCommands.blocked ||
    deleteObjects.blocked ||
    applyChanges.needsRefresh ||
    singlePlacement.needsRefresh ||
    singlePlacement.refreshing ||
    createRelease.isPending ||
    assistantWritePending;
  const manualWork = manualWorkspaceWork({
    tool,
    placementPanelOpen: patternSettingsOpen,
    hasPlan: projectHasPlan,
    hasPreview: Boolean(
      changePreview || patternPreview || recommendationPreview || brushPreview,
    ),
    hasPendingZone: Boolean(pendingZone),
    initialZoneDrafts: draftZones.length,
    brushStrokes: brushStrokes.length,
    brushDrawing,
    hasRowAxis: Boolean(rowAxis),
    rowDrawingPoints,
    placementAreaDrawing,
    zoneDrawing: Boolean(zoneDrawingMode),
    mutationPending: busy,
    brushPreviewPending: previewBrush.isPending,
    createPlanPending: createManualPlan.isPending,
    releasePending: createRelease.isPending,
    recoveryPending: writeOrRecoveryPending,
  });
  const navigationPending = manualWork.pending || assistantWritePending;
  const hasUnsavedWork =
    !planLocked && (manualWork.hasDraft || Boolean(assistant.proposal));
  const navigationBlocker = useWorkspaceNavigation({
    projectId,
    blocksLeaving: manualWork.blocksLeaving,
    hasUnsavedWork,
    assistantWritePending,
    hasAssistantProposal: Boolean(assistant.proposal),
  });

  const reloadAfterConflict = useCallback(async () => {
    // Keep the draft and the conflict visible until the current project is read.
    await refresh({ mapGeometry: true, strict: true });
    createManualPlan.reset();
    savePlacementZone.reset();
    saveManagedZones.reset();
    singlePlacement.clear();
    previewChanges.reset();
    previewPattern.reset();
    previewRecommendation.reset();
    previewBrush.reset();
    applyChanges.reset();
    deleteObjects.reset();
    undoChange.reset();
    redoChange.reset();
    createRelease.reset();
    mapEditInFlightRef.current = false;

    setRowAxis(undefined);
    setRowAxisSource(undefined);
    previewPattern.reset();
    discardZoneDrawing();
    setRecommendationOpen(false);
    previewRecommendation.reset();
    setBrushStrokes([]);
    previewBrush.reset();
    setSpeciesAssignmentOpen(false);
    editor.reset();
  }, [
    singlePlacement,
    applyChanges,
    createManualPlan,
    createRelease,
    deleteObjects,
    discardZoneDrawing,
    editor,
    previewBrush,
    previewChanges,
    previewPattern,
    previewRecommendation,
    redoChange,
    refresh,
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
      if (!['select', 'select_box', 'select_lasso'].includes(nextTool)) {
        if (assistantPending === 'thinking') stopAssistant();
        setAssistantOpen(false);
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
      if (
        changePreview ||
        assistant.proposal ||
        brushStrokes.length ||
        rowAxis
      ) {
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
      previewPattern.cancel();
      previewRecommendation.cancel();
      previewBrush.cancel();
      previewPattern.reset();
      setRecommendationOpen(false);
      previewRecommendation.reset();
      setBrushStrokes([]);
      previewBrush.reset();
      setSpeciesAssignmentOpen(false);
      previewChanges.reset();
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
      assistant.proposal,
      brushStrokes.length,
      rowAxis,
      planLocked,
      sourcePreview,
      projectHasPlan,
      previewPattern,
      previewRecommendation,
      previewBrush,
      previewChanges,
      editor,
      assistantPending,
      stopAssistant,
      setAssistantOpen,
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
    blocked: Boolean(
      writeOrRecoveryPending ||
      releaseOpen ||
      deleteSelectionOpen ||
      zonePendingDelete ||
      pendingTool ||
      navigationBlocker.state === 'blocked',
    ),
    onEscape: () => {
      if (zoneDrawingSession) {
        cancelZoneDrawing();
        return;
      }
      if (mapInspectTarget) {
        setMapInspectTarget(undefined);
        setMapHoverTarget(undefined);
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
        Boolean(patternPreview) ||
        brushStrokes.length > 0 ||
        speciesAssignmentOpen;
      if (!hasActiveOperation) {
        if (selectedIds.length) editor.clearSelection();
        return;
      }
      previewPattern.cancel();
      previewChanges.reset();
      previewRecommendation.cancel();
      previewBrush.cancel();
      previewPattern.reset();
      setRecommendationOpen(false);
      previewRecommendation.reset();
      setBrushStrokes([]);
      previewBrush.reset();
      setRowAxis(undefined);
      setRowAxisSource(undefined);
      setSpeciesAssignmentOpen(false);
      previewChanges.reset();
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
  const operationError =
    [
      createManualPlan,
      savePlacementZone,
      saveManagedZones,
      previewChanges,
      previewPattern,
      previewRecommendation,
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
      .sort((a, b) => b.submittedAt - a.submittedAt)[0]?.error ??
    mapGeometryQuery.error;
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
    hasRecommendation: Boolean(recommendationPreview),
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
    activateTool,
    activeLayer,
    activeLayerId,
    activeToolHint,
    addMapArea,
    applyChanges,
    assistant,
    assistantPreview,
    assistantZonePreview,
    brushDrawing,
    brushForm,
    brushOperation,
    brushPreview,
    brushSettings,
    brushStrokes,
    brushWidth,
    brushZones,
    buildingTargets,
    busy,
    changeIdeRightTab,
    changeMapMode,
    changePreview,
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
    focusGeometry,
    focusZones,
    growthHorizon,
    handleCoordinate,
    handleMapArea,
    handleMapExtent,
    handleMapStackSelect,
    handlePointerCoordinate,
    hasUnsavedWork,
    hiddenLayerNames,
    historyCommands,
    historyQuery,
    ideRightTab,
    initialExtent,
    inspectorView,
    issues,
    layers,
    leaveWorkspace,
    leftOpen,
    libraryOpen,
    manualWork,
    mapAreaTarget,
    mapGeometryMetadata: cad.vectorGeometryEnabled
      ? mapGeometryMetadata
      : undefined,
    cadBaseReady: cad.baseReady,
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
    metadataOnlyIds,
    moveLiveCheck,
    navigate,
    navigateWorkspace,
    navigationBlocker,
    navigationPending,
    openLeftPanel: editor.openSourceLayers,
    openRightPanel,
    operationError,
    panel,
    patternForm,
    patternPreview,
    patternSettingsOpen,
    pendingScene,
    pendingTool,
    pendingZone,
    placementAreaDrawing,
    placementCheck,
    placementMasksQuery,
    placementPreview,
    planLocked,
    planObjects,
    plantingZoneIds,
    plantingZoneUsage,
    previewBrush,
    previewChanges,
    previewDraft,
    previewPattern,
    previewRecommendation,
    previewSelectionLock,
    previewSelectionMoveLive,
    previewSpeciesAssignment,
    project,
    projectHasPlan,
    projectId,
    projectQuery,
    recommendationForm,
    recommendationOpen,
    recommendationPreview,
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
    rowAxis,
    rowAxisSource,
    rowDrawingPoints,
    rowInputMode,
    rowSettings,
    saveManagedZones,
    savePlacementZone,
    savingPlan,
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
    selectedLocked,
    selectedObject,
    selectedObjects,
    selectedPatternZoneIds,
    selectedPatternZones,
    setActiveLayerId,
    setBrushDrawing,
    setBrushOperation,
    setBrushStrokes,
    setBrushWidth,
    setBuildingScreenActive,
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
    setPatternSettingsOpen,
    setPendingScene,
    setPendingTool,
    setPendingZone,
    beginPlacementZoneDrawing,
    setRecommendationOpen,
    setReleaseOpen,
    setResultsOpen,
    setResultsTab,
    setReviewOpen,
    setRightOpen,
    setRowAxis,
    setRowAxisSource,
    setRowDrawingPoints,
    setRowInputMode,
    setSceneHorizon,
    setSelectedPatternZoneIds,
    setSingleShrubSpecies,
    setSingleTreeSpecies,
    setSpeciesAssignmentOpen,
    setSpeciesCatalogBrowsing,
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
    singleShrubSpecies,
    singleTreeSpecies,
    sourcePreview,
    sourceWarnings,
    sourceImportStatus: project?.import_status,
    speciesAssignmentOpen,
    speciesCatalogBrowsing,
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
