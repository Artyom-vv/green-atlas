import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiClientError, type BrushPreview, type BrushPreviewRequest, type BrushStroke, type ChangeSetPreview, type Layer, type PatternPreview, type PatternPreviewRequest, type PlacementCheck, type PlanChangeSetDraft, type PlanObject, type PlantingZoneAssignment, type RecommendationPreview, type RecommendationRequest, type ReleaseCreateRequest, type ReleasePackage } from '@green/api-client';
import { ChevronDown, ChevronUp, Copy, Crosshair, LockKeyhole, Maximize2, Minus, Move, Package, PanelRight, Plus, Redo2, Sprout, Trash2, Undo2 } from 'lucide-react';
import { useBlocker, useNavigate, useParams } from 'react-router-dom';
import { Button, Dialog, IconButton, InlineMessage, Progress, Select } from '@green/ui';
import { AppHeader } from '../domain-ui/AppHeader';
import { useProjectAssistant } from '../features/assistant/assistantContext';
import { ProjectAssistantSidebar, ProjectAssistantTrigger } from '../features/assistant/ProjectAssistantSidebar';
import { ReleasePanel } from '../domain-ui/ReleasePanel';
import { rowSketchFrame, type RowSketchSettings } from '../domain-ui/rowSketch';
import { ChangeSetReviewPanel } from '../domain-ui/ChangeSetReviewPanel';
import { BrushToolPanel } from '../domain-ui/BrushToolPanel';
import type { LiveBrushSettings } from '../domain-ui/liveBrushGeometry';
import { GroupInspector } from '../domain-ui/GroupInspector';
import { groupTransformDraft } from '../domain-ui/groupTransform';
import { HistoryPanel } from '../domain-ui/HistoryPanel';
import { EditorActions, EditorPanel } from '../domain-ui/EditorPanel';
import { GrowthHorizonSlider, type GrowthHorizon } from '../domain-ui/GrowthHorizonControl';
import { LayerInspector } from '../domain-ui/LayerInspector';
import { MapToolbar, type MapTool } from '../domain-ui/MapToolbar';
import { MapControlGroup } from '../domain-ui/MapControlGroup';
import { MapViewSwitch } from '../domain-ui/MapViewSwitch';
import { MapViewport, type MapAreaTarget, type MapExtent, type MapViewportHandle, type MapHoverItem, type MapHoverTarget } from '../domain-ui/MapViewport';
import { paddedMapExtent } from '../domain-ui/mapExtent';
import { ObjectInspector } from '../domain-ui/ObjectInspector';
import { PatternToolPanel } from '../domain-ui/PatternToolPanel';
import { PlantingZonesPanel } from '../domain-ui/PlantingZonesPanel';
import { PlantingZoneManager } from '../domain-ui/PlantingZoneManager';
import { PlantingsOverviewPanel } from '../domain-ui/PlantingsOverviewPanel';
import { assignmentFromGeometry } from '../domain-ui/plantingZones';
import { ProjectConflictNotice } from '../domain-ui/ProjectConflictNotice';
import { isProjectConflict } from '../domain-ui/projectConflict';
import { ProjectLayers } from '../domain-ui/ProjectLayers';
import { RecommendationPanel } from '../domain-ui/RecommendationPanel';
import type { BuildingScreenRequest } from '@green/api-client';
import { PlacementWorkspace } from '../domain-ui/PlacementWorkspace';
import { placementPreviewFrame } from '../domain-ui/placementPreviewFrame';
import { RecommendationReviewPanel } from '../domain-ui/RecommendationReviewPanel';
import { SceneResourceDiagnostics } from '../domain-ui/SceneResourceDiagnostics';
import type { SceneReviewHandle } from '../domain-ui/SceneReview';
import type { PlanViewState } from '../domain-ui/planViewState';
import { SpeciesAssignmentPanel } from '../domain-ui/SpeciesAssignmentPanel';
import type { WorkspaceDestination } from '../domain-ui/WorkspaceNavigation';
import { PlantingLibrary } from '../domain-ui/PlantingLibrary';
import { ZoneReview } from '../domain-ui/ZoneReview';
import { ToolWindow } from '../domain-ui/ToolWindow';
import { SpeciesPicker } from '../domain-ui/SpeciesPicker';
import { WorkspaceExplorer } from '../domain-ui/WorkspaceExplorer';
import { WorkspaceChecks } from '../domain-ui/WorkspaceChecks';
import { PlantingSchedule } from '../domain-ui/PlantingSchedule';
import '../features/workspace/editor-layout.css';
import { EditorHeader } from '../domain-ui/EditorHeader';
import { resolveInspectorView } from '../features/workspace/inspectorView';
import { useWorkspaceEditor } from '../features/workspace/useWorkspaceEditor';
import type { SelectionMode } from '../domain-ui/selection';
import { plantingCount } from '../domain-ui/countLabel';
import { invalidateMovePreviewGeneration, isCurrentMovePreviewGeneration, moveValidationFromPreview, type MoveLiveValidation } from '../domain-ui/moveLiveValidation';
import { metadataOnlyObjectIds } from '../domain-ui/planPresentation';
import { placementZoneSelection, zoneCollection, zoneExtent } from '../features/workspace/zoneSelection';
import { ResizeHandle } from '../features/workspace/ResizeHandle';
import { useEditorLayout } from '../features/workspace/useEditorLayout';

type WorkspacePanel = 'zones' | 'plantings' | 'issues' | 'history' | 'export' | null;
type MapGeometryMetadata = { returned_features?: number; total_matches?: number; truncated?: boolean };
type BufferedMapRequest = { projectId: string; extent: MapExtent; resolution: number };

const EMPTY_PLAN_OBJECTS: PlanObject[] = [];
const EMPTY_LAYERS: Layer[] = [];
const SceneReview = lazy(async () => ({ default: (await import('../domain-ui/SceneReview')).SceneReview }));
const message = (error: unknown) => error instanceof ApiClientError ? error.message : error instanceof Error ? error.message : 'Неизвестная ошибка';
function bufferedMapRequest(extent: MapExtent, resolution: number) {
  const width = Math.max(1, extent[2] - extent[0]);
  const height = Math.max(1, extent[3] - extent[1]);
  return {
    extent: [extent[0] - width * 0.8, extent[1] - height * 0.8, extent[2] + width * 0.8, extent[3] + height * 0.8].map((value) => Number(value.toFixed(1))) as MapExtent,
    resolution: Number(resolution.toFixed(3)),
  };
}

export function WorkspacePage() {
  const { projectId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const mapViewport = useRef<MapViewportHandle>(null);
  const sceneReview = useRef<SceneReviewHandle>(null);
  const sceneViewStateRef = useRef<PlanViewState | undefined>(undefined);
  const mapRequestRef = useRef<BufferedMapRequest | undefined>(undefined);
  const mapRequestTimerRef = useRef<number | undefined>(undefined);
  const placementTimerRef = useRef<number | undefined>(undefined);
  const placementAbortRef = useRef<AbortController | undefined>(undefined);
  const placementRequestRef = useRef(0);
  const patternAbortRef = useRef<AbortController | undefined>(undefined);
  const recommendationAbortRef = useRef<AbortController | undefined>(undefined);
  const brushAbortRef = useRef<AbortController | undefined>(undefined);
  const changeAbortRef = useRef<AbortController | undefined>(undefined);
  const changeRequestRef = useRef<PlanChangeSetDraft | undefined>(undefined);
  const brushLatestRequestRef = useRef<BrushPreviewRequest | undefined>(undefined);
  const movePreviewTimerRef = useRef<number | undefined>(undefined);
  const movePreviewAbortRef = useRef<AbortController | undefined>(undefined);
  const movePreviewRequestRef = useRef(0);
  const mapEditInFlightRef = useRef(false);
  const initializedProjectRef = useRef<string | undefined>(undefined);

  const [previewDraft, setPreviewDraft] = useState<{ previewId: string; draft: PlanChangeSetDraft }>();
  const [panel, setPanel] = useState<WorkspacePanel>(null);
  const [leftOpen, setLeftOpen] = useState(true);
  const layout = useEditorLayout();
  const [resultsTab, setResultsTab] = useState<'issues' | 'schedule' | 'history'>('issues');
  const [resultsOpen, setResultsOpen] = useState(false);
  const [pendingTool, setPendingTool] = useState<MapTool>();
  // The current task is the inspector. On a compact desktop it stays open
  // until the user explicitly collapses it; otherwise the first actionable
  // step is hidden behind an unexplained map-only screen.
  const [rightOpen, setRightOpen] = useState(true);
  const [activeLayerId, setActiveLayerId] = useState<string>();
  const [visibility, setVisibility] = useState<Record<string, boolean>>({});
  const [deleteSelectionOpen, setDeleteSelectionOpen] = useState(false);
  const assistant = useProjectAssistant();
  const setAssistantOpen = assistant.setOpen;
  const stopAssistant = assistant.stop;
  const assistantPending = assistant.pending;
  const assistantPreview = assistant.proposal?.stale ? undefined : assistant.proposal?.preview;
  const editor = useWorkspaceEditor(projectId);
  const { tool, selectedIds, preview: changePreview, setPreview: setEditorPreview } = editor;
  const setMapTool = editor.setTool;
  const [cursor, setCursor] = useState<[number, number]>();
  const [mapHoverTarget, setMapHoverTarget] = useState<MapHoverTarget>();
  const [mapInspectTarget, setMapInspectTarget] = useState<MapHoverTarget>();
  const [mapAreaTarget, setMapAreaTarget] = useState<MapAreaTarget>();
  const [placementCheck, setPlacementCheck] = useState<PlacementCheck>();
  const [moveLiveCheck, setMoveLiveCheck] = useState<MoveLiveValidation>();
  const [mapRequest, setMapRequest] = useState<BufferedMapRequest>();
  const [draftZones, setDraftZones] = useState<PlantingZoneAssignment[]>([]);
  const [zoneDrawingMode, setZoneDrawingMode] = useState<'new' | string>();
  const [zonePendingDelete, setZonePendingDelete] = useState<PlantingZoneAssignment>();
  const [release, setRelease] = useState<ReleasePackage>();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [rowAxis, setRowAxis] = useState<{ type: 'LineString'; coordinates: number[][] }>();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [dismissedOperationError, setDismissedOperationError] = useState<unknown>();
  const [rowInputMode, setRowInputMode] = useState<'pick' | 'draw' | 'ready'>('pick');
  const [rowDrawingPoints, setRowDrawingPoints] = useState(0);
  const [rowSettings, setRowSettings] = useState<RowSketchSettings>({ placementMode: 'count', count: 40, spacing: 6, side: 'center', lateralOffset: 3, startOffset: 0, endOffset: 0, kind: 'tree' });
  const [rowAxisSource, setRowAxisSource] = useState<{ type: 'dxf' | 'manual'; label: string }>();
  const [patternPreview, setPatternPreview] = useState<PatternPreview>();
  const [selectedPatternZoneIds, setSelectedPatternZoneIds] = useState<string[]>([]);
  const [placementAreaDrawing, setPlacementAreaDrawing] = useState(false);
  const [recommendationOpen, setRecommendationOpen] = useState(false);
  const [buildingScreenActive, setBuildingScreenActive] = useState(false);
  const [recommendationPreview, setRecommendationPreview] = useState<RecommendationPreview>();
  const [brushStrokes, setBrushStrokes] = useState<BrushStroke[]>([]);
  const [brushPreview, setBrushPreview] = useState<BrushPreview>();
  const [brushWidth, setBrushWidth] = useState(12);
  const [brushOperation, setBrushOperation] = useState<'add' | 'subtract'>('add');
  const [brushSettings, setBrushSettings] = useState<LiveBrushSettings>({ composition: 'trees', density: 'balanced', treeShare: .7, spacing: 6 });
  const [brushDrawing, setBrushDrawing] = useState(false);
  const [speciesAssignmentOpen, setSpeciesAssignmentOpen] = useState(false);
  const [speciesCatalogBrowsing, setSpeciesCatalogBrowsing] = useState(true);
  const [pendingZone, setPendingZone] = useState<{ zone: PlantingZoneAssignment; purpose: 'manage' | 'place' }>();
  const [zoneReviewOpen, setZoneReviewOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [patternSettingsOpen, setPatternSettingsOpen] = useState(false);
  const [toolCollapsed, setToolCollapsed] = useState(false);
  const [singleSettingsOpen, setSingleSettingsOpen] = useState(false);
  const [singleTreeSpecies, setSingleTreeSpecies] = useState<string>();
  const [singleShrubSpecies, setSingleShrubSpecies] = useState<string>();
  const growthHorizon = assistant.horizon ?? 0;
  const updateAssistantHorizon = assistant.setHorizon;
  const setGrowthHorizon = useCallback((value: GrowthHorizon) => updateAssistantHorizon(value ?? 0), [updateAssistantHorizon]);
  const syncAssistantSelection = assistant.setSelectedIds;
  useEffect(() => { syncAssistantSelection(selectedIds); }, [selectedIds, syncAssistantSelection]);
  const syncAssistantZones = assistant.setSelectedZoneIds;
  useEffect(() => { syncAssistantZones(selectedPatternZoneIds); }, [selectedPatternZoneIds, syncAssistantZones]);
  useEffect(() => {
    if (!assistantPreview) return;
    setSceneOpen(false);
    const frame = placementPreviewFrame([...(assistantPreview.additions ?? []), ...(assistantPreview.updates ?? [])]);
    if (frame) mapViewport.current?.fitGeometry(frame);
    else if (assistantPreview.deletion_ids?.length) mapViewport.current?.fitObjects(assistantPreview.deletion_ids);
  }, [assistantPreview]);
  const [sceneOpen, setSceneOpen] = useState(false);
  const [pendingScene, setPendingScene] = useState(false);
  const [mapRenderMode, setMapRenderMode] = useState<'design' | 'cad'>('design');
  const [mapPanActive, setMapPanActive] = useState(false);
  const [sceneMounted, setSceneMounted] = useState(false);
  const sceneHorizon = growthHorizon ?? 0;
  const setSceneHorizon = setGrowthHorizon;
  const [sceneRequestHorizon, setSceneRequestHorizon] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => setSceneRequestHorizon(sceneHorizon), 160);
    return () => window.clearTimeout(timer);
  }, [sceneHorizon]);
  const [sceneInitialViewState, setSceneInitialViewState] = useState<PlanViewState>();

  const changeMapMode = useCallback((mode: '2d' | '3d') => {
    if (mode === '3d') {
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
    const state = sceneReview.current?.getViewState() ?? sceneViewStateRef.current;
    if (state) {
      // Keep the latest 3D camera authoritative for both the map we are
      // returning to and the next 3D activation. Previously only a ref was
      // updated, so reopening 3D re-imported the stale entry camera.
      sceneViewStateRef.current = state;
      setSceneInitialViewState(state);
      mapViewport.current?.applyViewState(state);
    }
    setSceneOpen(false);
  }, [setMapTool]);

  useEffect(() => setMapInspectTarget(undefined), [tool]);

  const projectQuery = useQuery({ queryKey: ['workspace-project', projectId], queryFn: () => api.getProject(projectId, false), enabled: Boolean(projectId) });
  const project = projectQuery.data;
  const layers = project?.layers ?? EMPTY_LAYERS;
  const planObjects = project?.plan?.objects ?? EMPTY_PLAN_OBJECTS;
  const sourceWarnings = project?.source_file?.warnings ?? [];
  const projectHasPlan = Boolean(project?.plan);
  useEffect(() => {
    const request = assistant.focusRequest;
    if (!request?.objectIds.length) return;
    const known = new Set(planObjects.map(object => object.id));
    // Wait for the refreshed plan source. Fitting earlier would silently do
    // nothing because the newly added features are not installed in the map.
    if (!request.objectIds.every(id => known.has(id))) return;
    mapViewport.current?.fitObjects(request.objectIds);
  }, [assistant.focusRequest, planObjects]);
  // A DXF may legitimately have no outer site contour. Once geometry is
  // prepared it is still a working map: the operator draws the local area
  // instead of getting trapped in a read-only preview.
  const sourcePreview = Boolean(project && (!project.map_ready || project.import_status?.editability === 'read_only'));
  // A draft stays directly editable until the operator exports it for the
  // next process.
  const planLocked = project?.import_status?.editability === 'read_only';
  const historyQuery = useQuery({ queryKey: ['plan-history', projectId], queryFn: () => api.getPlanHistory(projectId), enabled: Boolean(projectId && projectHasPlan && !planLocked), staleTime: 0 });
  const speciesQuery = useQuery({ queryKey: ['species'], queryFn: () => api.listSpecies(), staleTime: Number.POSITIVE_INFINITY });
  // A route-param update renders once before its cleanup effect runs. Carry
  // the project ID with a viewport request so that brief transition can never
  // ask project B for project A's camera bounds.
  const activeMapRequest = mapRequest?.projectId === projectId ? mapRequest : undefined;
  const mapGeometryQuery = useQuery({
    queryKey: ['map-features', projectId, project?.geometry_version, activeMapRequest?.extent, activeMapRequest?.resolution],
    queryFn: ({ signal }) => api.getMapFeatures(projectId, activeMapRequest!.extent, activeMapRequest!.resolution, signal),
    enabled: Boolean(projectId && activeMapRequest),
    // Keeping the previous viewport prevents a white flash while navigating
    // one drawing. Never carry that visual cache into another project: its
    // DXF is a different source of truth, even if both snapshots share the
    // same geometry version.
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === projectId ? previous : undefined,
    staleTime: 30_000,
  });
  const mapGeometryMetadata = (mapGeometryQuery.data?.feature_collection as { metadata?: MapGeometryMetadata } | undefined)?.metadata;
  const sceneQuery = useQuery({ queryKey: ['plan-scene', projectId, project?.plan?.version, project?.geometry_version, sceneRequestHorizon], queryFn: ({ signal }) => api.getPlanScene(projectId, sceneRequestHorizon, signal), enabled: Boolean(sceneOpen && project?.map_ready), placeholderData: (previous, query) => query?.queryKey[1] === projectId ? previous : undefined, staleTime: Number.POSITIVE_INFINITY });

  const refresh = useCallback(async ({ mapGeometry = false }: { mapGeometry?: boolean } = {}) => {
    const queries = [
      queryClient.invalidateQueries({ queryKey: ['workspace-project', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['plan-history', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['projects'] }),
    ];
    // Manual edits are rendered by the dedicated plan overlay. Refetching a
    // 12k-feature CAD viewport after every tree click makes a dense drawing
    // visibly stutter even though its terrain has not changed. The map
    // snapshot changes only after the selected areas have been persisted and
    // the manual editor is opened, so that transition opts in explicitly.
    if (mapGeometry) queries.push(queryClient.invalidateQueries({ queryKey: ['map-features', projectId] }));
    await Promise.all(queries);
  }, [projectId, queryClient]);

  useEffect(() => {
    // Route parameters can change without unmounting this page. A selection,
    // placement preview or buffered request from project A must never affect
    // project B in that same SPA session.
    initializedProjectRef.current = undefined;
    changeRequestRef.current = undefined;
    changeAbortRef.current?.abort();
    mapRequestRef.current = undefined;
    mapEditInFlightRef.current = false;
    placementRequestRef.current += 1;
    placementAbortRef.current?.abort();
    patternAbortRef.current?.abort();
    recommendationAbortRef.current?.abort();
    brushAbortRef.current?.abort();
    movePreviewRequestRef.current += 1;
    movePreviewAbortRef.current?.abort();
    if (mapRequestTimerRef.current !== undefined) window.clearTimeout(mapRequestTimerRef.current);
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    if (movePreviewTimerRef.current !== undefined) window.clearTimeout(movePreviewTimerRef.current);
    mapRequestTimerRef.current = undefined;
    placementTimerRef.current = undefined;
    movePreviewTimerRef.current = undefined;
    setPanel(null);
    setLeftOpen(true);
    setResultsOpen(false);
    setResultsTab('issues');
    setPendingTool(undefined);
    setMapPanActive(false);
    setRightOpen(true);
    setActiveLayerId(undefined);
    setVisibility({});
    setDeleteSelectionOpen(false);
    setCursor(undefined);
    setMapHoverTarget(undefined);
    setMapInspectTarget(undefined);
    setPlacementCheck(undefined);
    setMoveLiveCheck(undefined);
    setMapRequest(undefined);
    setDraftZones([]);
    setZoneDrawingMode(undefined);
    setZonePendingDelete(undefined);
    setRelease(undefined);
    setReleaseOpen(false);
    setReviewOpen(false);
    setDismissedOperationError(undefined);
    setRowInputMode('pick');
    setRowDrawingPoints(0);
    setRowAxis(undefined);
    setRowAxisSource(undefined);
    setPatternPreview(undefined);
    setSelectedPatternZoneIds([]);
    setPlacementAreaDrawing(false);
    setRecommendationOpen(false);
    setRecommendationPreview(undefined);
    setBrushStrokes([]);
    setBrushPreview(undefined);
    setSpeciesAssignmentOpen(false);
    setSceneOpen(false);
    setSceneMounted(false);
  }, [projectId]);

  useEffect(() => {
    if (!project || initializedProjectRef.current === project.id) return;
    initializedProjectRef.current = project.id;
    const projectZones = project.planting_zones ?? [];
    setDraftZones(projectZones);
    setSelectedPatternZoneIds(projectZones.flatMap(zone => zone.id ? [zone.id] : []));
    setPanel(project.plan ? null : 'zones');
    // Initial framing is owned by MapViewport, after its sources are ready.
  }, [project]);

  useEffect(() => () => {
    changeAbortRef.current?.abort();
    if (mapRequestTimerRef.current !== undefined) window.clearTimeout(mapRequestTimerRef.current);
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    placementAbortRef.current?.abort();
    patternAbortRef.current?.abort();
    recommendationAbortRef.current?.abort();
    brushAbortRef.current?.abort();
    if (movePreviewTimerRef.current !== undefined) window.clearTimeout(movePreviewTimerRef.current);
    movePreviewAbortRef.current?.abort();
  }, []);

  const initialExtent = useMemo(() => paddedMapExtent(project?.source_file?.bounds, 20, sourcePreview ? 0.3 : 0), [project?.source_file?.bounds, sourcePreview]);
  const activeLayer = useMemo(() => layers.find((layer) => layer.id === activeLayerId), [activeLayerId, layers]);
  const hiddenLayerNames = useMemo(() => layers.filter((layer) => visibility[layer.id] === false).map((layer) => layer.source_name), [layers, visibility]);
  const selectedId = selectedIds.length === 1 ? selectedIds[0] : undefined;
  const selectedObject = useMemo(() => planObjects.find((item) => item.id === selectedId), [planObjects, selectedId]);
  const selectedPatternZones = useMemo(() => (project?.planting_zones ?? []).filter(zone => zone.id && selectedPatternZoneIds.includes(zone.id)), [project?.planting_zones, selectedPatternZoneIds]);
  const buildingTargets = useQuery({
    queryKey: ['building-screen-targets', projectId, selectedPatternZoneIds, project?.state_version],
    queryFn: ({ signal }) => api.getBuildingScreenTargets(projectId, selectedPatternZoneIds, signal),
    enabled: recommendationOpen && buildingScreenActive && selectedPatternZoneIds.length > 0,
  });
  const screenGeometry = recommendationOpen && buildingScreenActive ? buildingTargets.data?.geometry : undefined;
  const focusGeometry = useMemo(() => screenGeometry ?? zoneCollection(selectedPatternZones), [screenGeometry, selectedPatternZones]);
  useEffect(() => { if (screenGeometry && !recommendationPreview) mapViewport.current?.fitGeometry(screenGeometry); }, [screenGeometry, recommendationPreview]);
  useEffect(() => { if (!recommendationOpen) setBuildingScreenActive(false); }, [recommendationOpen]);
  const brushZones = useMemo(() => (project?.planting_zones ?? []).filter(zone => zone.id && selectedPatternZoneIds.includes(zone.id)), [project?.planting_zones, selectedPatternZoneIds]);
  const plantingZoneIds = useMemo(() => (project?.planting_zones ?? []).flatMap((zone) => zone.id ? [zone.id] : []), [project?.planting_zones]);
  const selectPatternZones = useCallback((ids: string[]) => { setSelectedPatternZoneIds([...new Set(ids)]); setMapAreaTarget(undefined); }, []);
  const focusZones = (zones: PlantingZoneAssignment[]) => {
    const extent = zoneExtent(zones);
    if (sceneOpen && extent) sceneReview.current?.fitExtent(extent);
    else if (zones.length) mapViewport.current?.fitGeometry(zoneCollection(zones));
  };
  const plantingZoneUsage = useMemo(() => planObjects.reduce<Record<string, number>>((usage, object) => {
    if (object.planting_zone_id) usage[object.planting_zone_id] = (usage[object.planting_zone_id] ?? 0) + 1;
    return usage;
  }, {}), [planObjects]);
  const selectedObjects = useMemo(() => {
    const ids = new Set(selectedIds);
    return planObjects.filter((item) => item.id && ids.has(item.id));
  }, [planObjects, selectedIds]);
  const selectedLocked = selectedObjects.some(object => object.locked);
  const selectedIdsKey = [...selectedIds].sort().join(':');
  const speciesShortlistQuery = useQuery({ queryKey: ['species-shortlist', projectId, selectedIdsKey], queryFn: () => api.shortlistSpecies(projectId, selectedIds), enabled: Boolean(speciesAssignmentOpen && selectedIds.length && new Set(selectedObjects.map(object => object.kind)).size === 1 && !selectedLocked), staleTime: 30_000 });
  const selectedPatternZonesKey = [...selectedPatternZoneIds].sort().join(',');
  const zoneSpeciesShortlistQuery = useQuery({ queryKey: ['species-shortlist-zones', projectId, selectedPatternZonesKey], queryFn: () => api.shortlistSpecies(projectId, { zoneIds: selectedPatternZoneIds }), enabled: Boolean(project?.plan && selectedPatternZoneIds.length && (tool === 'pattern_fill' || tool === 'pattern_row')), staleTime: 30_000 });
  const placementMasksQuery = useQuery({ queryKey: ['placement-masks', projectId], queryFn: () => api.listPlacementMasks(projectId), enabled: Boolean(project?.plan && tool === 'pattern_fill'), staleTime: 5 * 60_000 });
  const speciesNames = useMemo(() => new Map((speciesQuery.data ?? []).map((item) => [item.id, item.common_name])), [speciesQuery.data]);
  const issues = project?.plan?.issues ?? [];
  const metadataOnlyIds = useMemo(() => metadataOnlyObjectIds(project?.plan?.issues ?? []), [project?.plan?.issues]);
  const issueCount = issues.length;
  const placementPreview = useMemo(() => {
    if (!cursor || (tool !== 'add_tree' && tool !== 'add_shrub')) return undefined;
    const kind = tool === 'add_tree' ? 'tree' : 'shrub';
    const check = placementCheck && Math.abs(placementCheck.x - cursor[0]) < 0.01 && Math.abs(placementCheck.y - cursor[1]) < 0.01 ? placementCheck : undefined;
    return { coordinate: cursor, radius: kind === 'tree' ? 1.6 : 0.65, status: check?.status ?? 'unknown' } as const;
  }, [cursor, placementCheck, tool]);

  const openLeftPanel = useCallback(() => {
    setLeftOpen(true);
    setRightOpen(true);
  }, []);
  const openRightPanel = useCallback(() => { setRightOpen(true); }, []);
  const closeRightPanel = useCallback(() => setRightOpen(false), []);
  const navigateWorkspace = useCallback((destination: WorkspaceDestination) => {
    if (destination === 'issues') { setResultsTab('issues'); setResultsOpen(true); return; }
    setActiveLayerId(undefined);
    setMapAreaTarget(undefined);
    setPanel(destination);
    if (destination === 'zones') setDraftZones(project?.planting_zones ?? []);
    openRightPanel();
  }, [openRightPanel, project?.planting_zones]);

  const handleMapExtent = useCallback((extent: MapExtent, resolution: number) => {
    const previous = mapRequestRef.current;
    if (previous?.projectId === projectId) {
      const width = previous.extent[2] - previous.extent[0];
      const height = previous.extent[3] - previous.extent[1];
      const insetX = width * 0.18;
      const insetY = height * 0.18;
      const covered = Math.abs(previous.resolution - resolution) < 0.25
        && extent[0] >= previous.extent[0] + insetX && extent[1] >= previous.extent[1] + insetY
        && extent[2] <= previous.extent[2] - insetX && extent[3] <= previous.extent[3] - insetY;
      if (covered) return;
    }
    const next = { projectId, ...bufferedMapRequest(extent, resolution) };
    mapRequestRef.current = next;
    if (mapRequestTimerRef.current !== undefined) window.clearTimeout(mapRequestTimerRef.current);
    // Wheel/toolbar zoom emits a short burst of camera updates. Keep drawing
    // the buffered snapshot immediately, then ask the server once for the
    // settled viewport instead of racing two expensive DXF fragments.
    mapRequestTimerRef.current = window.setTimeout(() => {
      mapRequestTimerRef.current = undefined;
      void queryClient.cancelQueries({ queryKey: ['map-features', projectId] }).then(() => {
        // A newer camera position may have arrived while the previous network
        // request was being aborted. Only the latest buffered viewport may
        // start the next fetch.
        if (mapRequestRef.current === next) setMapRequest(next);
      });
    }, 180);
  }, [projectId, queryClient]);

  const handlePointerCoordinate = useCallback((coordinate?: [number, number]) => {
    const adding = tool === 'add_tree' || tool === 'add_shrub';
    if (!coordinate || !adding || !projectHasPlan || sourcePreview) {
      setCursor((current) => current ? undefined : current);
      if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
      placementAbortRef.current?.abort();
      setPlacementCheck(undefined);
      return;
    }
    setCursor(coordinate);
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    placementTimerRef.current = window.setTimeout(() => {
      placementAbortRef.current?.abort();
      const controller = new AbortController();
      placementAbortRef.current = controller;
      const requestId = placementRequestRef.current + 1;
      placementRequestRef.current = requestId;
      void api.checkPlacement(projectId, { kind: tool === 'add_tree' ? 'tree' : 'shrub', x: coordinate[0], y: coordinate[1] }, controller.signal)
        .then((next) => { if (requestId === placementRequestRef.current) setPlacementCheck(next); })
        .catch(() => undefined);
      placementTimerRef.current = undefined;
    }, 120);
  }, [projectHasPlan, projectId, sourcePreview, tool]);

  const createManualPlan = useMutation({
    mutationFn: async () => {
      await api.savePlantingZones(projectId, draftZones);
      return api.createManualPlan(projectId);
    },
    onSuccess: async (nextProject) => {
      queryClient.setQueryData(['workspace-project', projectId], nextProject);
      const nextZones = nextProject.planting_zones ?? [];
      setDraftZones(nextZones);
      setSelectedPatternZoneIds(nextZones.flatMap((zone) => zone.id ? [zone.id] : []));
      setPanel(null);
      editor.setTool('select');
      await refresh({ mapGeometry: true });
      openRightPanel();
    },
  });
  const zoneReviewQuery = useQuery({ queryKey: ['zone-review', projectId, pendingZone?.zone, project?.geometry_version], queryFn: () => api.previewPlantingZone(projectId, pendingZone!.zone), enabled: Boolean(pendingZone), retry: false });
  const reviewZones = useMemo(() => pendingZone ? [...(project?.planting_zones ?? []).filter(zone => zone.id !== pendingZone.zone.id), pendingZone.zone] : project?.planting_zones ?? [], [pendingZone, project?.planting_zones]);
  const savePlacementZone = useMutation({
    mutationFn: ({ zone }: { zone: PlantingZoneAssignment; nextTool?: MapTool }) => api.savePlantingZones(projectId, [...(project?.planting_zones ?? []), zone]),
    onSuccess: async (nextProject, { zone, nextTool = 'pattern_fill' }) => {
      setPendingZone(undefined); setZoneReviewOpen(false);
      queryClient.setQueryData(['workspace-project', projectId], nextProject);
      setDraftZones(nextProject.planting_zones ?? []);
      setSelectedPatternZoneIds(current => zone.id ? [...new Set([...current, zone.id])] : current);
      setPlacementAreaDrawing(false);
      setPatternSettingsOpen(true);
      editor.setTool(nextTool);
      await refresh({ mapGeometry: true });
      openRightPanel();
    },
  });
  const saveManagedZones = useMutation({
    mutationFn: ({ zones }: { zones: PlantingZoneAssignment[]; focusId?: string }) => api.savePlantingZones(projectId, zones),
    onSuccess: async (nextProject, variables) => {
      setPendingZone(undefined); setZoneReviewOpen(false);
      queryClient.setQueryData(['workspace-project', projectId], nextProject);
      setDraftZones(nextProject.planting_zones ?? []);
      const available = new Set((nextProject.planting_zones ?? []).map(zone => zone.id));
      setSelectedPatternZoneIds(current => [...new Set([...current.filter(id => available.has(id)), ...(variables.focusId ? [variables.focusId] : [])])]);
      setZoneDrawingMode(undefined);
      setZonePendingDelete(undefined);
      editor.setTool('select');
      await refresh({ mapGeometry: true });
    },
  });
  const addObject = useMutation({ mutationFn: ({ kind, coordinate }: { kind: 'tree' | 'shrub'; coordinate: [number, number] }) => api.addPlanObject(projectId, { kind, x: coordinate[0], y: coordinate[1], species_revision_id: kind === 'tree' ? singleTreeSpecies : singleShrubSpecies, size_class: 'standard' }), onSuccess: async () => { await refresh(); } });
  const previewChanges = useMutation({
    mutationFn: (draft: PlanChangeSetDraft) => {
      changeAbortRef.current?.abort();
      const controller = new AbortController();
      changeAbortRef.current = controller;
      changeRequestRef.current = draft;
      return api.previewPlanChanges(projectId, draft, controller.signal);
    },
    onSuccess: (preview, draft) => {
      if (changeRequestRef.current !== draft || changeAbortRef.current?.signal.aborted) return;
      setPreviewDraft({ previewId: preview.id!, draft });
      setReviewOpen(true); editor.setTool('select'); editor.setPreview(preview); setActiveLayerId(undefined); setPanel(null); openRightPanel();
    },
  });
  const previewPattern = useMutation({
    mutationFn: (request: PatternPreviewRequest) => {
      patternAbortRef.current?.abort();
      const controller = new AbortController();
      patternAbortRef.current = controller;
      return api.previewPlanPattern(projectId, request, controller.signal);
    },
    onSuccess: (result) => {
      if (patternAbortRef.current?.signal.aborted) return;
      setPatternPreview(result);
      setPatternSettingsOpen(true);
      setToolCollapsed(false);
      editor.setPreview(result.change_set ?? undefined);
      const frame = placementPreviewFrame(result.change_set?.additions ?? []);
      if (frame) mapViewport.current?.fitGeometry(frame);
    },
  });
  const previewRecommendation = useMutation({
    mutationFn: (request: RecommendationRequest | BuildingScreenRequest) => {
      recommendationAbortRef.current?.abort();
      const controller = new AbortController();
      recommendationAbortRef.current = controller;
      return 'screen_side' in request ? api.previewBuildingScreen(projectId, request, controller.signal) : api.previewRecommendation(projectId, request as RecommendationRequest, controller.signal);
    },
    onSuccess: (result) => {
      if (recommendationAbortRef.current?.signal.aborted) return;
      setRecommendationPreview(result);
      setPatternSettingsOpen(true);
      editor.setPreview(result.change_set ?? undefined);
      const frame = placementPreviewFrame(result.change_set?.additions ?? []);
      if (frame) mapViewport.current?.fitGeometry(frame);
      setActiveLayerId(undefined);
      setPanel(null);
      openRightPanel();
    },
  });
  const previewBrush = useMutation({
    mutationFn: (request: BrushPreviewRequest) => {
      brushAbortRef.current?.abort();
      brushLatestRequestRef.current = request;
      const controller = new AbortController();
      brushAbortRef.current = controller;
      return api.previewBrush(projectId, request, controller.signal);
    },
    onSuccess: (result, request) => {
      if (request !== brushLatestRequestRef.current || brushAbortRef.current?.signal.aborted) return;
      setBrushPreview(result);
      editor.setPreview(result.change_set ?? undefined);
    },
  });
  useEffect(() => {
    if (tool !== 'brush') return;
    brushLatestRequestRef.current = undefined;
    brushAbortRef.current?.abort();
    setBrushPreview(undefined);
    setEditorPreview(undefined);
  }, [brushSettings, brushWidth, selectedPatternZonesKey, setEditorPreview, tool]);
  const applyChanges = useMutation({ mutationFn: (explicitPreview?: ChangeSetPreview) => {
    const preview = explicitPreview ?? changePreview;
    if (!preview) throw new Error('Предпросмотр изменений недоступен');
    return api.applyPlanChanges(projectId, preview);
  }, onSuccess: async (result) => { setReviewOpen(false); setPatternPreview(undefined); setRecommendationOpen(false); setRecommendationPreview(undefined); setBrushStrokes([]); setBrushPreview(undefined); setRowAxis(undefined); setRowAxisSource(undefined); setSpeciesAssignmentOpen(false); editor.setPreview(undefined); editor.setTool('select'); editor.select([...(result.added_ids ?? []), ...(result.updated_ids ?? [])], 'replace'); await refresh(); } });
  const deleteObjects = useMutation({ mutationFn: (ids: string[]) => api.deletePlanObjects(projectId, ids), onSuccess: async () => { editor.clearSelection(); await refresh(); } });
  const undoChange = useMutation({ mutationFn: () => api.undoPlanChange(projectId), onSuccess: async () => { editor.clearSelection(); editor.setTool('select'); await refresh(); } });
  const redoChange = useMutation({ mutationFn: () => api.redoPlanChange(projectId), onSuccess: async () => { editor.clearSelection(); editor.setTool('select'); await refresh(); } });
  const createRelease = useMutation({ mutationFn: (request: ReleaseCreateRequest) => api.createRelease(projectId, request), onSuccess: setRelease });
  const busy = addObject.isPending || savePlacementZone.isPending || saveManagedZones.isPending || previewChanges.isPending || previewPattern.isPending || previewRecommendation.isPending || applyChanges.isPending || deleteObjects.isPending || undoChange.isPending || redoChange.isPending;
  // Export captures one durable version of the plan. Do not let a normal map
  // click race that snapshot and surface an avoidable version conflict.
  const editorBusy = busy || createRelease.isPending || Boolean(assistant.proposal) || assistant.pending === 'preparing' || assistant.pending === 'applying' || assistant.pending === 'undoing';
  const savingPlan = applyChanges.isPending || addObject.isPending || saveManagedZones.isPending || savePlacementZone.isPending || createManualPlan.isPending || deleteObjects.isPending || undoChange.isPending || redoChange.isPending;
  const hasUnsavedWork = !planLocked && Boolean(pendingZone || changePreview || assistant.proposal || brushStrokes.length || rowAxis || placementAreaDrawing || zoneDrawingMode || (!projectHasPlan && draftZones.length));
  const navigationBlocker = useBlocker(({ currentLocation, nextLocation }) => (Boolean(pendingZone || changePreview || brushStrokes.length || rowAxis || placementAreaDrawing || zoneDrawingMode || (!projectHasPlan && draftZones.length)) || savingPlan || assistant.pending === 'applying' || assistant.pending === 'undoing' || (Boolean(assistant.proposal) && !nextLocation.pathname.startsWith(`/projects/${projectId}/`))) && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!hasUnsavedWork && !savingPlan) return;
    const preventLoss = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', preventLoss);
    return () => window.removeEventListener('beforeunload', preventLoss);
  }, [hasUnsavedWork, savingPlan]);


  const reloadAfterConflict = useCallback(async () => {
    // React Query keeps a mutation error until it is reset. Reloading only
    // the project data would therefore leave the old conflict notice mounted
    // and the editor in the stale add/move mode.
    createManualPlan.reset();
    savePlacementZone.reset();
    saveManagedZones.reset();
    addObject.reset();
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
    placementRequestRef.current += 1;
    placementAbortRef.current?.abort();
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    placementTimerRef.current = undefined;
    setPlacementCheck(undefined);
    setCursor(undefined);
    setRowAxis(undefined);
    setRowAxisSource(undefined);
    setPatternPreview(undefined);
    setPlacementAreaDrawing(false);
    setRecommendationOpen(false);
    setRecommendationPreview(undefined);
    setBrushStrokes([]);
    setBrushPreview(undefined);
    setSpeciesAssignmentOpen(false);
    editor.reset();
    await refresh();
  }, [addObject, applyChanges, createManualPlan, createRelease, deleteObjects, editor, previewBrush, previewChanges, previewPattern, previewRecommendation, redoChange, refresh, saveManagedZones, savePlacementZone, undoChange]);

  const addMapArea = useCallback((geometry: PlantingZoneAssignment['geometry'], label: string, sourceId?: string) => {
    setDraftZones((current) => {
      if (sourceId && current.some((zone) => zone.id === sourceId)) return current;
      return [...current, assignmentFromGeometry(geometry, current.length + 1, label, sourceId)];
    });
    setPanel('zones');
    openRightPanel();
  }, [openRightPanel]);

  const handleMapArea = useCallback((target: MapAreaTarget, mode: SelectionMode = 'replace') => {
    // The picker is map-local state. Clear it as soon as a target is chosen,
    // while leaving the task inspector mounted for an active placement tool.
    setMapHoverTarget(undefined);
    setMapInspectTarget(undefined);
    if (!projectHasPlan) {
      if (target.selectable && target.geometry) addMapArea(target.geometry, target.label, target.sourceId);
      return;
    }
    if (!target.plantingZoneId && target.selectable && target.geometry && ['pattern_fill', 'pattern_row', 'brush'].includes(tool) && !planLocked && !savePlacementZone.isPending) {
      const zone = assignmentFromGeometry(target.geometry, (project?.planting_zones?.length ?? 0) + 1, target.label, target.sourceId);
      setMapAreaTarget(target);
      savePlacementZone.mutate({ zone, nextTool: tool === 'pattern_row' || tool === 'brush' ? tool : 'pattern_fill' });
      return;
    }
    if (panel === 'zones') {
      setMapAreaTarget(target);
      return;
    }
    if (target.plantingZoneId) {
      setSelectedPatternZoneIds((current) => {
        if (mode === 'add') return [...new Set([...current, target.plantingZoneId!])];
        if (mode === 'subtract') return current.filter((id) => id !== target.plantingZoneId);
        return [target.plantingZoneId!];
      });
    }
    setMapAreaTarget(target);
    setActiveLayerId(undefined);
    // Pattern/row/brush inspectors are the current task and must survive a
    // map target choice. Selection mode still clears object selection so the
    // inspector cannot silently describe a different task.
    const taskInspectorActive = tool === 'pattern_fill' || tool === 'pattern_row' || tool === 'brush' || tool === 'draw_area';
    if (!taskInspectorActive) setPanel(null);
    editor.clearSelection();
    openRightPanel();
  }, [addMapArea, editor, openRightPanel, panel, planLocked, project?.planting_zones?.length, projectHasPlan, savePlacementZone, tool]);

  const handleMapStackSelect = useCallback((item: MapHoverItem) => {
    if (item.target) {
      handleMapArea(item.target);
      return;
    }
    if (!item.preview) return;
    setActiveLayerId(undefined);
    setPanel(null);
    openRightPanel();
    if (planObjects.some((object) => object.id === item.preview!.objectId)) editor.select([item.preview.objectId], 'replace');
  }, [editor, handleMapArea, openRightPanel, planObjects]);

  const activateTool = useCallback((nextTool: MapTool) => {
    if (editorBusy) return;
    // The hand is a view mode, not a replacement planting operation.
    if (nextTool === 'pan') { setMapPanActive(value => !value); return; }
    if (!['select', 'select_box', 'select_lasso'].includes(nextTool)) {
      if (assistantPending === 'thinking') stopAssistant();
      setAssistantOpen(false);
    }
    setMapPanActive(false);
    if (nextTool === 'pattern_fill') setPatternSettingsOpen(true);
    if (nextTool === 'add_tree' || nextTool === 'add_shrub') setSingleSettingsOpen(true);
    setToolCollapsed(false);
    if (nextTool === tool) {
      setActiveLayerId(undefined);
      setPanel(null);
      setSpeciesAssignmentOpen(false);
      openRightPanel();
      return;
    }
    if (changePreview || assistant.proposal || brushStrokes.length || rowAxis) { setPendingTool(nextTool); return; }
    if ((planLocked || sourcePreview) && ['add_tree', 'add_shrub', 'pattern_row', 'pattern_fill', 'brush', 'move', 'copy', 'draw_area'].includes(nextTool)) return;
    if (!projectHasPlan && ['add_tree', 'add_shrub', 'pattern_row', 'pattern_fill', 'brush', 'move', 'copy', 'select_box', 'select_lasso'].includes(nextTool)) return;
    patternAbortRef.current?.abort();
    recommendationAbortRef.current?.abort();
    brushAbortRef.current?.abort();
    setPatternPreview(undefined);
    setRecommendationOpen(false);
    setRecommendationPreview(undefined);
    setBrushStrokes([]);
    setBrushPreview(undefined);
    setSpeciesAssignmentOpen(false);
    editor.setPreview(undefined);
    if (nextTool === 'pattern_row') setRowInputMode('pick');
    if (nextTool !== 'pattern_row') {
      setRowAxis(undefined);
      setRowAxisSource(undefined);
    }
    if (nextTool === 'pattern_row' || nextTool === 'pattern_fill' || nextTool === 'brush') {
      editor.clearSelection();
      setActiveLayerId(undefined);
      setPanel(null);
      openRightPanel();
    }
    if (nextTool === 'pattern_fill' || nextTool === 'pattern_row' || nextTool === 'brush') {
      setSelectedPatternZoneIds(current => placementZoneSelection(current, plantingZoneIds));
    }
    editor.setTool(tool === nextTool && nextTool !== 'select' ? 'select' : nextTool);
  }, [brushStrokes.length, changePreview, assistant.proposal, assistantPending, stopAssistant, setAssistantOpen, editor, editorBusy, openRightPanel, planLocked, plantingZoneIds, projectHasPlan, rowAxis, sourcePreview, tool]);

  const previewSelectionTransform = (mode: 'move' | 'copy', coordinate: [number, number]) => {
    const plan = project?.plan;
    if (!plan || !selectedObjects.length) return;
    const copiedGroupId = mode === 'copy' ? `group-${globalThis.crypto.randomUUID()}` : undefined;
    const draft = groupTransformDraft(plan.version, selectedObjects, mode, coordinate, copiedGroupId);
    if (draft) previewChanges.mutate(draft);
  };

  const previewSelectionMoveLive = useCallback((coordinate?: [number, number]) => {
    const plan = project?.plan;
    if (!coordinate || (tool !== 'move' && tool !== 'select') || !plan || !selectedObjects.length || planLocked) {
      invalidateMovePreviewGeneration(movePreviewRequestRef);
      if (movePreviewTimerRef.current !== undefined) window.clearTimeout(movePreviewTimerRef.current);
      movePreviewTimerRef.current = undefined;
      movePreviewAbortRef.current?.abort();
      setMoveLiveCheck(undefined);
      return;
    }
    const draft = groupTransformDraft(plan.version, selectedObjects, 'move', coordinate);
    if (!draft) return;
    const requestId = invalidateMovePreviewGeneration(movePreviewRequestRef);
    movePreviewAbortRef.current?.abort();
    setMoveLiveCheck({ status: 'checking', reason: 'Проверяем новое положение', objectStatuses: {} });
    if (movePreviewTimerRef.current !== undefined) window.clearTimeout(movePreviewTimerRef.current);
    movePreviewTimerRef.current = window.setTimeout(() => {
      const controller = new AbortController();
      movePreviewAbortRef.current = controller;
      void api.previewPlanChanges(projectId, draft, controller.signal).then((preview) => {
        if (!isCurrentMovePreviewGeneration(movePreviewRequestRef, requestId)) return;
        setMoveLiveCheck(moveValidationFromPreview(preview));
      }).catch((error) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (isCurrentMovePreviewGeneration(movePreviewRequestRef, requestId)) setMoveLiveCheck({ status: 'unknown', reason: 'Не удалось проверить положение', objectStatuses: {} });
      });
      movePreviewTimerRef.current = undefined;
    }, 120);
  }, [planLocked, project?.plan, projectId, selectedObjects, tool]);

  const previewSelectionLock = (locked: boolean) => {
    const plan = project?.plan;
    if (!plan || !selectedObjects.length) return;
    previewChanges.mutate({
      base_plan_version: plan.version,
      source: 'group',
      label: locked ? `Закрепление объектов (${selectedObjects.length})` : `Снятие закрепления (${selectedObjects.length})`,
      policy: 'all_or_nothing',
      operations: selectedObjects.flatMap((object) => object.id ? [{ type: 'update' as const, object_id: object.id, changes: { locked } }] : []),
    });
  };

  const previewSpeciesAssignment = (revisionId: string, sizeClass: 'sapling' | 'standard' | 'large') => {
    const plan = project?.plan;
    if (!plan || !selectedObjects.length) return;
    previewChanges.mutate({
      base_plan_version: plan.version,
      source: selectedObjects.length > 1 ? 'group' : 'manual',
      label: selectedObjects.length > 1 ? `Порода для группы (${selectedObjects.length})` : 'Назначение породы',
      policy: 'all_or_nothing',
      operations: selectedObjects.flatMap((object) => object.id ? [{ type: 'update' as const, object_id: object.id, changes: { species_revision_id: revisionId, size_class: sizeClass } }] : []),
    });
  };

  const handleCoordinate = (coordinate: [number, number]) => {
    if (editorBusy || planLocked || !projectHasPlan) return;
    if (tool === 'add_tree' || tool === 'add_shrub') {
      const checkMatches = placementCheck && Math.abs(placementCheck.x - coordinate[0]) < 0.01 && Math.abs(placementCheck.y - coordinate[1]) < 0.01;
      if (!(tool === 'add_tree' ? singleTreeSpecies : singleShrubSpecies)) { setSingleSettingsOpen(true); return; }
      if (!checkMatches || !placementCheck.allowed || mapEditInFlightRef.current) return;
      mapEditInFlightRef.current = true;
      addObject.mutate(
        { kind: tool === 'add_tree' ? 'tree' : 'shrub', coordinate },
        { onSettled: () => { mapEditInFlightRef.current = false; } },
      );
    }
    if ((tool === 'move' || tool === 'copy' || tool === 'select') && selectedObjects.length && !mapEditInFlightRef.current) {
      invalidateMovePreviewGeneration(movePreviewRequestRef);
      if (movePreviewTimerRef.current !== undefined) window.clearTimeout(movePreviewTimerRef.current);
      movePreviewTimerRef.current = undefined;
      movePreviewAbortRef.current?.abort();
      setMoveLiveCheck(undefined);
      mapEditInFlightRef.current = true;
      previewSelectionTransform(tool === 'copy' ? 'copy' : 'move', coordinate);
      mapEditInFlightRef.current = false;
    }
  };

  useEffect(() => {
    const handleHistoryShortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select, [contenteditable="true"]') || target?.closest('[role="dialog"], .project-assistant') || releaseOpen || deleteSelectionOpen || zonePendingDelete || pendingTool || navigationBlocker.state === 'blocked') return;
      if (event.key === 'Escape') {
        event.preventDefault();
        if (mapInspectTarget) {
          setMapInspectTarget(undefined);
          setMapHoverTarget(undefined);
          return;
        }
        if (sceneOpen) {
          setSceneOpen(false);
          return;
        }
        const hasActiveOperation = previewChanges.isPending || tool !== 'select'
          || Boolean(changePreview)
          || recommendationOpen
          || Boolean(recommendationPreview)
          || Boolean(patternPreview)
          || brushStrokes.length > 0
          || speciesAssignmentOpen;
        if (!hasActiveOperation) {
          if (selectedIds.length) editor.clearSelection();
          return;
        }
        patternAbortRef.current?.abort();
        changeRequestRef.current = undefined;
        changeAbortRef.current?.abort();
        previewChanges.reset();
        recommendationAbortRef.current?.abort();
        brushAbortRef.current?.abort();
        setPatternPreview(undefined);
        setRecommendationOpen(false);
        setRecommendationPreview(undefined);
        setBrushStrokes([]);
        setBrushPreview(undefined);
        setRowAxis(undefined);
        setRowAxisSource(undefined);
        setSpeciesAssignmentOpen(false);
        editor.setPreview(undefined);
        editor.setTool('select');
        return;
      }
      if ((event.key === 'Enter' || event.code === 'F2') && changePreview?.can_apply && !editorBusy) {
        event.preventDefault();
        setReviewOpen(true);
        return;
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedIds.length && !editorBusy && !selectedLocked && !hasUnsavedWork) {
        event.preventDefault();
        setDeleteSelectionOpen(true);
        return;
      }
      const modifier = event.metaKey || event.ctrlKey;
      if (!modifier || planLocked || editorBusy) return;
      if (event.code === 'KeyZ' && !event.shiftKey && historyQuery.data?.can_undo && !undoChange.isPending) {
        event.preventDefault();
        undoChange.mutate();
      }
      if ((event.code === 'KeyY' || (event.code === 'KeyZ' && event.shiftKey)) && historyQuery.data?.can_redo && !redoChange.isPending) {
        event.preventDefault();
        redoChange.mutate();
      }
    };
    window.addEventListener('keydown', handleHistoryShortcut);
    return () => window.removeEventListener('keydown', handleHistoryShortcut);
  }, [applyChanges, brushStrokes.length, changePreview, editor, editorBusy, selectedLocked, hasUnsavedWork, releaseOpen, deleteSelectionOpen, zonePendingDelete, pendingTool, navigationBlocker.state, historyQuery.data?.can_redo, historyQuery.data?.can_undo, mapInspectTarget, patternPreview, planLocked, previewChanges, recommendationOpen, recommendationPreview, redoChange, sceneOpen, selectedIds.length, speciesAssignmentOpen, tool, undoChange]);

  if (projectQuery.isLoading) return <div className="app-shell"><AppHeader /><main className="center-status"><Progress label="Загрузка рабочей области" /></main></div>;
  if (!project) return <div className="app-shell"><AppHeader /><main className="center-status"><InlineMessage tone="error">{message(projectQuery.error)}</InlineMessage></main></div>;

  const operationError = [createManualPlan, savePlacementZone, saveManagedZones, addObject, previewChanges, previewPattern, previewRecommendation, previewBrush, applyChanges, deleteObjects, undoChange, redoChange].filter(mutation => mutation.error && !(mutation.error instanceof Error && mutation.error.name === 'AbortError')).sort((a, b) => b.submittedAt - a.submittedAt)[0]?.error ?? mapGeometryQuery.error;
  const showMapStatus = Boolean(
    (placementCheck && (tool === 'add_tree' || tool === 'add_shrub'))
    || (moveLiveCheck && (tool === 'move' || tool === 'select'))
    || mapGeometryQuery.isFetching
    || mapGeometryMetadata?.truncated,
  );
  const mapInfoTarget = mapInspectTarget ?? mapHoverTarget;

  const selectFromExplorer = (ids: string[], fit = true) => {
    if (hasUnsavedWork || editorBusy) return;
    activateTool('select');
    editor.select(ids, 'replace'); setActiveLayerId(undefined); setPanel(null); setMapAreaTarget(undefined); openRightPanel();
    if (fit) requestAnimationFrame(() => { if (sceneOpen) sceneReview.current?.fitSelection(); else mapViewport.current?.fitObjects(ids); });
  };
  const leaveWorkspace = (path: string) => {
    navigate(path);
  };
  const showResults = (tab: typeof resultsTab) => { setResultsOpen(current => resultsTab === tab ? !current : true); setResultsTab(tab); };
  const inspectorView = resolveInspectorView({ recommendationOpen, panel, hasPlan: projectHasPlan, sourcePreview, hasLayer: Boolean(activeLayer), tool, drawingPlacementArea: placementAreaDrawing, hasPattern: Boolean(patternPreview), hasChange: Boolean(changePreview), hasRecommendation: Boolean(recommendationPreview), assigningSpecies: speciesAssignmentOpen, selectionCount: selectedIds.length, hasArea: Boolean(mapAreaTarget) });
  const toolNames: Record<MapTool, string> = { select: 'Выбор', pan: 'Перемещение карты', select_box: 'Выбор рамкой', select_lasso: 'Выбор лассо', pattern_fill: 'Разместить посадки', pattern_row: 'Посадки вдоль линии', brush: 'Кисть посадок', add_tree: 'Посадить дерево', add_shrub: 'Посадить кустарник', move: 'Перемещение посадок', copy: 'Копирование посадок', draw_area: 'Граница участка' };
  const activeToolHint = tool === 'brush' ? (selectedPatternZoneIds.length ? 'Проведите по выбранным участкам' : 'Выберите участки в проекте') : tool === 'pattern_row' && patternPreview ? (patternPreview.accepted_count ? 'Проверенные позиции на карте. Добавьте их или измените условия' : 'Мест не найдено. Измените условия ряда') : tool === 'pattern_row' ? (rowInputMode === 'draw' ? 'Поставьте точки и завершите линию' : rowAxis ? 'Эскиз на карте. Настройте ряд и проверьте места' : 'Выберите линию DXF или нарисуйте свою') : tool === 'pattern_fill' ? (selectedPatternZoneIds.length ? `Участков для расчёта: ${selectedPatternZoneIds.length}` : 'Выберите участки в проекте') : tool === 'draw_area' ? 'Поставьте точки и замкните контур' : tool === 'move' || tool === 'copy' ? 'Укажите новое место на карте' : tool === 'add_tree' || tool === 'add_shrub' ? 'Выберите допустимое место на карте' : '';
  const patternTool = <PatternToolPanel calculating={previewPattern.isPending} onCancelCalculation={() => { patternAbortRef.current?.abort(); previewPattern.reset(); }} guided={tool !== 'pattern_row'} mode={tool === 'pattern_row' ? 'row' : 'fill'} zones={project.planting_zones ?? []} species={speciesQuery.data ?? []} shortlist={zoneSpeciesShortlistQuery.data} shortlistLoading={zoneSpeciesShortlistQuery.isLoading} placementMasks={placementMasksQuery.data} axis={rowAxis} axisSource={rowAxisSource} axisMode={rowInputMode} axisDrawingPoints={rowDrawingPoints} onRowSettingsChange={setRowSettings} onAxisModeChange={mode => { mapViewport.current?.abortRowDrawing(); setRowInputMode(mode); setRowDrawingPoints(0); patternAbortRef.current?.abort(); setPatternPreview(undefined); editor.setPreview(undefined); }} onFinishAxis={() => mapViewport.current?.finishRowDrawing()} onFitAxis={() => rowAxis && mapViewport.current?.fitGeometry(rowSketchFrame(rowAxis, rowSettings))} onReverseAxis={() => { if (rowAxis) setRowAxis({ ...rowAxis, coordinates: [...rowAxis.coordinates].reverse() }); setPatternPreview(undefined); editor.setPreview(undefined); }} selectedZoneIds={selectedPatternZoneIds} drawingZone={placementAreaDrawing} preview={patternPreview} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} loading={previewPattern.isPending || applyChanges.isPending || savePlacementZone.isPending} error={zoneSpeciesShortlistQuery.error ? message(zoneSpeciesShortlistQuery.error) : placementMasksQuery.error ? message(placementMasksQuery.error) : undefined} onSelectedZoneIdsChange={selectPatternZones} onDrawZone={() => { setPatternSettingsOpen(false); setPlacementAreaDrawing(true); setMapAreaTarget(undefined); editor.setTool('draw_area'); }} onPreview={(draft) => { if (sceneOpen) changeMapMode('2d'); previewPattern.mutate({ ...draft, base_plan_version: project.plan!.version } as PatternPreviewRequest); }} onApply={() => { if (patternPreview?.change_set?.can_apply) applyChanges.mutate(patternPreview.change_set); }} onResetPreview={() => { patternAbortRef.current?.abort(); setPatternPreview(undefined); editor.setPreview(undefined); }} onCancel={() => { patternAbortRef.current?.abort(); setPatternPreview(undefined); editor.setPreview(undefined); setRowAxis(undefined); setRowAxisSource(undefined); setPlacementAreaDrawing(false); editor.setTool('select'); }} />;
  return <div className="landscape-editor" style={{ '--assistant-width': `${assistant.width}px`, '--editor-right': `${layout.width}px`, '--editor-project-height': `${layout.projectHeight}px`, '--editor-results-height': `${layout.resultsHeight}px` } as CSSProperties}>
    <EditorHeader name={project.name} onBack={() => leaveWorkspace('/projects')}>
      <IconButton icon={Undo2} label="Отменить" variant="ghost" disabled={!historyQuery.data?.can_undo || editorBusy || Boolean(changePreview)} onClick={() => undoChange.mutate()} />
      <IconButton icon={Redo2} label="Повторить" variant="ghost" disabled={!historyQuery.data?.can_redo || editorBusy || Boolean(changePreview)} onClick={() => redoChange.mutate()} />
      <span className="editor-header__divider" />
      {savingPlan || assistant.pending === 'applying' ? <span role="status">Сохраняем…</span> : null}
      <ProjectAssistantTrigger onOpen={() => editor.setTool('select')} disabled={Boolean(changePreview || brushStrokes.length || rowAxis || patternSettingsOpen)} />
      <IconButton icon={PanelRight} label={assistant.open ? 'Показать объекты проекта' : tool === 'pattern_fill' && patternSettingsOpen ? 'Свернуть размещение' : rightOpen ? 'Скрыть боковую панель' : 'Показать боковую панель'} variant="ghost" onClick={() => { if (assistant.open) { assistant.setOpen(false); openRightPanel(); } else if (tool === 'pattern_fill' && patternSettingsOpen) setPatternSettingsOpen(false); else if (rightOpen) closeRightPanel(); else openRightPanel(); }} />
      <Button icon={Package} variant="primary" disabled={!projectHasPlan || editorBusy} onClick={() => setReleaseOpen(true)}>Выпуск</Button>
    </EditorHeader>
    <main ref={layout.ref} className={`editor-layout ${assistant.open ? 'has-assistant' : ''} ${tool === 'pattern_fill' && patternSettingsOpen ? 'is-placing' : ''} ${rightOpen ? '' : 'is-right-collapsed'}`}>

      <ProjectAssistantSidebar />
      <div className="editor-map-stage">
      <section className="editor-map" onMouseLeave={() => setMapHoverTarget(undefined)}>
        {!sceneOpen && activeToolHint ? <div className="editor-active-tool" role="status"><strong>{toolNames[tool]}</strong><span>{activeToolHint}</span></div> : null}
        {selectedPatternZones.length ? <Button className="editor-scope-focus" variant="secondary" controlSize="compact" icon={Crosshair} onClick={() => focusZones(selectedPatternZones)}>{`Участки: ${selectedPatternZones.length}`}</Button> : null}
<MapViewport rowResultReady={Boolean(patternPreview)} rowAxis={rowAxis} rowSettings={rowSettings} rowInputMode={rowInputMode} onRowDrawingPoints={setRowDrawingPoints} changeDraft={assistantPreview ? assistant.proposal?.draft : previewDraft?.previewId === changePreview?.id ? previewDraft?.draft : undefined} metadataOnlyIds={metadataOnlyIds} editPending={previewChanges.isPending} interactionDisabled={editorBusy || planLocked} brushSettings={brushSettings} brushZones={brushZones} onBrushGesture={(active) => { setBrushDrawing(active); if (active) { brushLatestRequestRef.current = undefined; brushAbortRef.current?.abort(); setBrushPreview(undefined); editor.setPreview(undefined); } }} renderMode={mapRenderMode} key={projectId} ref={mapViewport} geometry={mapGeometryQuery.data?.feature_collection as Record<string, unknown> | undefined} geometryRevision={project.geometry_version} initialExtent={initialExtent} objects={planObjects} growthHorizon={growthHorizon} draftPlantingZones={!projectHasPlan ? draftZones : reviewZones} hiddenLayerNames={hiddenLayerNames} selectedIds={selectedIds} highlightedPlantingZoneIds={selectedPatternZoneIds} focusGeometry={focusGeometry} placementPreview={placementPreview} changePreview={assistantPreview ?? changePreview} liveMoveValidation={moveLiveCheck} tool={mapPanActive ? 'pan' : tool} brushStrokes={brushStrokes} brushEnabled={selectedPatternZoneIds.length > 0 && !editorBusy && (brushOperation === 'subtract' || ((brushSettings.composition === 'shrubs' || Boolean(brushSettings.treeSpeciesId)) && (brushSettings.composition === 'trees' || Boolean(brushSettings.shrubSpeciesId))))} brushWidthM={brushWidth} brushOperation={brushOperation} onDrawArea={(geometry) => {
          if (projectHasPlan && panel === 'zones' && zoneDrawingMode && !planLocked) {
            const previous = zoneDrawingMode === 'new' ? undefined : (project.planting_zones ?? []).find((zone) => zone.id === zoneDrawingMode);
            const zone = previous ? { ...previous, geometry } : assignmentFromGeometry(geometry, (project.planting_zones?.length ?? 0) + 1, `Участок ${(project.planting_zones?.length ?? 0) + 1}`);
            setPendingZone({ zone, purpose: 'manage' }); setZoneReviewOpen(true); setZoneDrawingMode(undefined); editor.setTool('select');
            return;
          }
          if (projectHasPlan && placementAreaDrawing && !planLocked) {
            const zone = assignmentFromGeometry(geometry, (project.planting_zones?.length ?? 0) + 1, `Участок ${project.planting_zones?.length ?? 0}`);
            setPendingZone({ zone, purpose: 'place' }); setZoneReviewOpen(true); setPlacementAreaDrawing(false); editor.setTool('select');
            return;
          }
          if (projectHasPlan || planLocked) return;
          addMapArea(geometry, `Ручной участок ${draftZones.length + 1}`);
          editor.setTool('select');
        }} onDrawAxis={(geometry, source) => { setRowInputMode('ready'); setRowAxis(geometry); setRowAxisSource(source); setPatternPreview(undefined); editor.setPreview(undefined); openRightPanel(); }} onDrawBrush={(stroke) => { setBrushStrokes((current) => [...current, stroke]); setBrushPreview(undefined); editor.setPreview(undefined); openRightPanel(); }} onMapArea={handleMapArea} onMapHover={setMapHoverTarget} onMapInspect={setMapInspectTarget} onSelect={(id, mode = 'replace') => {
          if (!projectHasPlan) return;
          if (!id) { if (mode === 'replace') editor.clearSelection(); return; }
          editor.select([id], mode);
          setMapHoverTarget(undefined);
          setMapInspectTarget(undefined);
          setMapAreaTarget(undefined);
          setActiveLayerId(undefined);
          setPanel(null);
          openRightPanel();
        }} onSelectMany={(ids, mode) => {
          if (!projectHasPlan) return;
          editor.select(ids, mode);
          editor.setTool('select');
          setActiveLayerId(undefined);
          setPanel(null);
          openRightPanel();
        }} onCoordinate={handleCoordinate} onTranslateSelectionEnd={handleCoordinate} onMoveCoordinate={previewSelectionMoveLive} onPointerCoordinate={handlePointerCoordinate} onExtentChange={handleMapExtent} />
        {!sceneOpen && (!selectedObjects.length || mapInfoTarget?.kind === 'preview') && mapInfoTarget && (tool === 'select' || tool === 'pattern_fill') ? <div className={`map-hover-hint ${mapInspectTarget ? 'is-pinned' : ''}`} style={{ '--map-hover-x': `${mapInfoTarget.pixel[0] + 14}px`, '--map-hover-y': `${mapInfoTarget.pixel[1] + 14}px` } as CSSProperties} role="status" aria-label={mapInfoTarget.kind === 'preview' ? 'Проверка новой позиции' : mapInspectTarget ? 'Выбор объекта карты' : 'Информация об объекте карты'} onMouseDown={(event) => event.stopPropagation()}>{mapInfoTarget.items.map((item) => {
          const content = <><strong>{item.label}</strong><span>{item.detail}</span></>;
          const className = `map-hover-hint__item ${item.preview || item.target?.selectable ? 'is-selectable' : 'is-reference'}`;
          return mapInspectTarget
            ? <button className={className} key={item.id} type="button" aria-label={`${item.preview ? 'Показать проверку' : 'Выбрать'} ${item.label}`} onClick={(event) => { event.stopPropagation(); handleMapStackSelect(item); setMapInspectTarget(undefined); }}>{content}</button>
            : <div className={className} key={item.id}>{content}</div>;
        })}</div> : null}
        {tool === 'pattern_row' && project.plan ? <ToolWindow title="Ряд посадок" collapsed={toolCollapsed} onToggle={() => setToolCollapsed(value => !value)}>{patternTool}</ToolWindow> : null}
        {tool === 'brush' && project.plan ? <ToolWindow title="Кисть посадок" collapsed={toolCollapsed} onToggle={() => setToolCollapsed(value => !value)}><BrushToolPanel species={speciesQuery.data ?? []} applying={applyChanges.isPending} settings={brushSettings} onSettingsChange={setBrushSettings} strokes={brushStrokes} zones={project.planting_zones ?? []} zoneIds={selectedPatternZoneIds} width={brushWidth} operation={brushOperation} preview={brushPreview} loading={brushDrawing || previewBrush.isPending || applyChanges.isPending}  onZoneIdsChange={(ids) => { selectPatternZones(ids); setBrushPreview(undefined); editor.setPreview(undefined); }} onWidth={setBrushWidth} onOperation={setBrushOperation} onPreview={(draft) => previewBrush.mutate({ ...draft, base_plan_version: project.plan!.version })} onApply={() => setReviewOpen(true)} onClear={() => { brushLatestRequestRef.current = undefined; brushAbortRef.current?.abort(); previewBrush.reset(); setBrushStrokes([]); setBrushPreview(undefined); editor.setPreview(undefined); }} onCancel={() => { brushAbortRef.current?.abort(); setBrushStrokes([]); setBrushPreview(undefined); editor.setPreview(undefined); editor.setTool('select'); }} /></ToolWindow> : null}
        {pendingZone && !zoneReviewOpen ? <div className="map-workflow-actions"><strong>Новый контур не сохранён</strong><Button variant="primary" onClick={() => setZoneReviewOpen(true)}>Проверить участок</Button></div> : null}
        {tool === 'pattern_fill' && !patternSettingsOpen ? <div className="map-workflow-actions"><Button variant="primary" onClick={() => setPatternSettingsOpen(true)}>{patternPreview || recommendationPreview ? 'Вернуться к результату' : 'Продолжить размещение'}</Button></div> : null}
        {(tool === 'add_tree' || tool === 'add_shrub') ? <div className="map-workflow-actions"><strong>{(tool === 'add_tree' ? singleTreeSpecies : singleShrubSpecies) ? 'Выберите место на карте' : 'Сначала выберите породу'}</strong><Button variant="secondary" onClick={() => setSingleSettingsOpen(true)}>Порода посадки</Button></div> : null}
        {projectHasPlan ? <div className="editor-tool-rail"><MapToolbar mapMode={sceneOpen ? '3d' : '2d'} tool={mapPanActive ? 'pan' : sceneOpen ? 'select' : tool} onTool={next => { if (sceneOpen && (next === 'select' || next === 'pan')) { setMapPanActive(next === 'pan'); return; } if (sceneOpen) changeMapMode('2d'); activateTool(next); }} editable={!sourcePreview && !planLocked && !editorBusy} canDelete={selectedIds.length > 0 && !selectedLocked && !hasUnsavedWork} onDelete={() => setDeleteSelectionOpen(true)} /></div> : null}
        {!sceneOpen && selectedIds.length > 0 && !changePreview && ['select', 'move', 'copy', 'select_box', 'select_lasso'].includes(tool) ? <div className="editor-selection-actions" role="group" aria-label="Действия над выделением"><span>{selectedIds.length}</span><IconButton icon={Sprout} label="Назначить виды" variant="ghost" disabled={editorBusy || planLocked || selectedLocked} onClick={() => { setActiveLayerId(undefined); setPanel(null); setSpeciesAssignmentOpen(true); openRightPanel(); }} /><IconButton icon={Move} label="Переместить выбранное" variant="ghost" disabled={editorBusy || selectedObjects.some(o => o.locked)} onClick={() => activateTool('move')} /><IconButton icon={Copy} label="Копировать выбранное" variant="ghost" disabled={editorBusy} onClick={() => activateTool('copy')} /><IconButton icon={LockKeyhole} label={selectedLocked ? 'Открепить выбранное' : 'Закрепить выбранное'} variant="ghost" disabled={editorBusy} onClick={() => previewSelectionLock(!selectedLocked)} /></div> : null}
        {!sceneOpen ? <div className="editor-map-navigation">
          <MapControlGroup className="editor-zoom" orientation="vertical" label="Масштаб карты"><IconButton icon={Plus} label="Увеличить" variant="ghost" onClick={() => mapViewport.current?.zoomIn()} /><IconButton icon={Minus} label="Уменьшить" variant="ghost" onClick={() => mapViewport.current?.zoomOut()} /><IconButton icon={Maximize2} label="Показать весь чертёж" variant="ghost" onClick={() => mapViewport.current?.fit()} /></MapControlGroup>
          {project.plan ? <MapControlGroup className="editor-plan-focus" orientation="vertical" label="План озеленения"><IconButton icon={Crosshair} label="Показать посадки" variant="ghost" onClick={() => mapViewport.current?.fitPlan()} /></MapControlGroup> : null}
        </div> : null}
        {showMapStatus ? <div className="editor-map-status">{placementCheck && (tool === 'add_tree' || tool === 'add_shrub') ? <span className={`placement-check placement-check--${placementCheck.status}`} role="status">{placementCheck.reason}</span> : null}{moveLiveCheck && (tool === 'move' || tool === 'select') ? <span className={`placement-check placement-check--${moveLiveCheck.status === 'soft_conflict' ? 'unknown' : moveLiveCheck.status}`} role="status">{moveLiveCheck.reason}</span> : null}{mapGeometryQuery.isFetching ? <span className="map-stream-status">Обновляем карту</span> : null}{mapGeometryMetadata?.truncated ? <span className="map-lod-warning" role="status">Приблизьте карту, чтобы увидеть детали</span> : null}</div> : null}
        {busy ? <div className="map-busy"><Progress label={previewChanges.isPending || previewPattern.isPending || previewRecommendation.isPending ? 'Проверяем размещение' : 'Сохраняем изменения'} /></div> : null}

{sceneMounted ? <Suspense fallback={<div className="scene-review scene-review--loading"><Progress label="Загрузка 3D" /></div>}><SceneReview showGrowthControl={!assistant.open} ref={sceneReview} active={sceneOpen} zones={project.planting_zones ?? []} selectedZoneIds={selectedPatternZoneIds} snapshot={sceneQuery.data} horizon={sceneHorizon} selectedIds={selectedIds} issues={issues} loading={sceneHorizon !== sceneRequestHorizon || sceneQuery.isLoading || sceneQuery.isFetching} error={sceneQuery.error ? message(sceneQuery.error) : undefined} initialViewState={sceneInitialViewState} onViewStateChange={(state) => { sceneViewStateRef.current = state; }} onHorizon={setSceneHorizon} onSelect={(id) => { if (!mapPanActive) selectFromExplorer([id], false); }} onMoveTarget={projectHasPlan && !planLocked && !editorBusy && !selectedLocked ? handleCoordinate : undefined} /></Suspense> : null}
        <div className="editor-map-presentation" role="group" aria-label="Отображение карты">
        {!sceneOpen ? <Select aria-label="Подложка карты" value={mapRenderMode} onChange={event => setMapRenderMode(event.target.value as 'design' | 'cad')}><option value="design">Проектный вид</option><option value="cad">Исходный DXF</option></Select> : null}
        {project.map_ready ? <MapViewSwitch mode={sceneOpen ? '3d' : '2d'} onChange={mode => { if (mode === '3d' && hasUnsavedWork) { setPendingScene(true); return; } changeMapMode(mode); }} /> : null}
        </div>
        {changePreview && !patternPreview && !brushPreview && !recommendationPreview && !reviewOpen ? <div className="editor-change-prompt" role="status"><span>{changePreview.can_apply ? 'Изменения готовы' : 'Изменение не прошло проверку'}</span><Button variant="secondary" onClick={() => setReviewOpen(true)}>{changePreview.can_apply ? 'Проверить и применить' : 'Посмотреть причину'}</Button></div> : null}
      </section>
      <nav className="editor-results-tabs" aria-label="Результаты проекта"><button type="button" aria-pressed={resultsOpen && resultsTab === 'issues'} onClick={() => showResults('issues')}>Проверка <small>{issueCount}</small></button><button type="button" aria-pressed={resultsOpen && resultsTab === 'schedule'} onClick={() => showResults('schedule')}>Ведомость</button><button type="button" aria-pressed={resultsOpen && resultsTab === 'history'} onClick={() => showResults('history')}>История</button><button type="button" aria-label={resultsOpen ? 'Свернуть результаты' : 'Развернуть результаты'} aria-expanded={resultsOpen} onClick={() => setResultsOpen(!resultsOpen)}>{resultsOpen ? <ChevronDown size={16} /> : <ChevronUp size={16} />}</button></nav>
      {resultsOpen ? <ResizeHandle label="Высота результатов" orientation="horizontal" reverse value={layout.resultsHeight} min={layout.minResults} max={layout.maxResults} onChange={value => layout.set('resultsHeight', value)} onReset={() => layout.reset('resultsHeight')} /> : null}
      {assistant.open ? <div className="assistant-growth"><label>Рост посадок</label><GrowthHorizonSlider value={growthHorizon} onChange={value => { setGrowthHorizon(value); assistant.setHorizon(value ?? 0); }} /><output>{growthHorizon ? `${growthHorizon} лет` : 'Сейчас'}</output></div> : null}
      <section className="editor-results" hidden={!resultsOpen}>
        {resultsTab === 'issues' ? <WorkspaceChecks objects={planObjects} disabled={planLocked || editorBusy || Boolean(changePreview) || brushStrokes.length > 0 || Boolean(rowAxis)} issues={issues} onLocate={selectFromExplorer} onAssign={(ids) => { selectFromExplorer(ids); setSpeciesAssignmentOpen(true); }} /> : null}
        {resultsTab === 'schedule' ? <PlantingSchedule objects={planObjects} speciesNames={speciesNames} onSelect={selectFromExplorer} /> : null}
        {resultsTab === 'history' ? <HistoryPanel history={historyQuery.data} busy={undoChange.isPending || redoChange.isPending || Boolean(changePreview)} onUndo={() => undoChange.mutate()} onRedo={() => redoChange.mutate()} /> : null}
      </section>
      </div>
      <aside className={`editor-dock ${leftOpen ? '' : 'is-project-collapsed'}`} aria-label="Проект и инструмент">
        <ResizeHandle label="Ширина боковой панели" orientation="vertical" value={layout.width} min={layout.minWidth} max={layout.maxWidth} reverse onChange={value => layout.set('width', value)} onReset={() => layout.reset('width')} className="editor-dock-resize" />
        <div className="editor-project"><WorkspaceExplorer zones={project.planting_zones ?? []} objects={planObjects} selectedIds={selectedIds} selectedZoneIds={selectedPatternZoneIds} speciesNames={speciesNames} sourceName={project.source_file?.name ?? project.name} sourceCount={layers.reduce((count, layer) => count + layer.object_count, 0)} collapsed={!leftOpen} zoneSelectionDisabled={Boolean(changePreview) || editorBusy || planLocked || Boolean(zoneDrawingMode)} disabled={hasUnsavedWork || editorBusy} onZonesChange={selectPatternZones} onSelect={(ids) => selectFromExplorer(ids, false)} onZone={(zone) => focusZones([zone])} onManagePlantings={() => setLibraryOpen(true)} onManageZones={() => navigateWorkspace('zones')} onSource={() => leaveWorkspace(`/projects/${projectId}/setup`)} onClose={() => setLeftOpen(value => !value)} layers={<ProjectLayers embedded layers={layers} visibility={visibility} activeLayerId={activeLayerId} onVisibility={(id, visible) => setVisibility((current) => ({ ...current, [id]: visible }))} onSelect={(id) => { setActiveLayerId((current) => current === id ? undefined : id); setPanel(null); openRightPanel(); }} />} /></div>
        {leftOpen ? <ResizeHandle label="Высота раздела проекта" orientation="horizontal" value={layout.projectHeight} min={layout.minProject} max={layout.maxProject} onChange={value => layout.set('projectHeight', value)} onReset={() => layout.reset('projectHeight')} /> : null}
      <div className="editor-inspector">
        <div className="editor-inspector__body">
        {inspectorView === 'new-zones' && !project.plan ? <PlantingZonesPanel onClose={closeRightPanel} assignments={draftZones} drawingManual={tool === 'draw_area'} saving={createManualPlan.isPending} error={createManualPlan.error ? message(createManualPlan.error) : undefined} onRemove={(id) => setDraftZones((current) => current.filter((item) => item.id !== id))} onSave={() => createManualPlan.mutate()} onManual={() => editor.setTool('draw_area')} onCancelManual={() => editor.setTool('select')} /> : null}

        {inspectorView === 'overview' && project.plan ? <PlantingsOverviewPanel mapMode={sceneOpen ? '3d' : '2d'} objects={planObjects} onPlace={() => { if (sceneOpen) changeMapMode('2d'); activateTool('pattern_fill'); }} onFit={() => sceneOpen ? sceneReview.current?.fitPlantings() : mapViewport.current?.fitPlan()} onClose={closeRightPanel} /> : null}
        {inspectorView === 'layer' && activeLayer ? <LayerInspector layer={activeLayer} visible={visibility[activeLayer.id] !== false} onVisibility={(visible) => setVisibility((current) => ({ ...current, [activeLayer.id]: visible }))} onFit={() => mapViewport.current?.fitLayer(activeLayer.source_name)} /> : null}

        {inspectorView === 'source' ? <EditorPanel title={planLocked ? "Только просмотр" : "Исходный DXF"}><p className="editor-panel__hint">{planLocked ? "Для редактирования посадок загрузите полный ZIP-пакет выпуска." : "Назначьте роли слоям, затем выберите участок на карте."}</p>{sourceWarnings.map(warning => <InlineMessage key={warning} tone="warning">{warning}</InlineMessage>)}<EditorActions grid><Button variant="secondary" onClick={openLeftPanel}>Открыть слои</Button><Button variant="primary" onClick={() => navigate(`/projects/${projectId}/${planLocked ? 'import' : 'setup'}`)}>{planLocked ? 'Загрузить полный ZIP' : 'Исходные данные'}</Button></EditorActions></EditorPanel> : null}






        {inspectorView === 'object' && selectedObject ? <ObjectInspector onLock={!planLocked && !editorBusy ? previewSelectionLock : undefined} mapMode={sceneOpen ? '3d' : '2d'} onFit={() => sceneOpen ? sceneReview.current?.fitSelection() : mapViewport.current?.fitObjects(selectedIds)} onMove={() => { if (sceneOpen) changeMapMode('2d'); activateTool('move'); }} object={selectedObject} speciesName={selectedObject.species_revision_id ? speciesNames.get(selectedObject.species_revision_id) : undefined} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} editable={!planLocked && !editorBusy && !selectedObject.locked} onSpecies={() => setSpeciesAssignmentOpen(true)} onDelete={() => setDeleteSelectionOpen(true)} /> : null}
        {inspectorView === 'group' ? <GroupInspector mapMode={sceneOpen ? '3d' : '2d'} onFit={() => sceneOpen ? sceneReview.current?.fitSelection() : mapViewport.current?.fitObjects(selectedIds)} onMove={() => { if (sceneOpen) changeMapMode('2d'); activateTool('move'); }} objects={selectedObjects} issues={issues} disabled={editorBusy || planLocked} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} onSpecies={() => setSpeciesAssignmentOpen(true)} onCopy={() => { if (sceneOpen) changeMapMode('2d'); activateTool('copy'); }} onLock={previewSelectionLock} onDelete={() => setDeleteSelectionOpen(true)} /> : null}
        </div>
      </div>
      </aside>
      <SceneResourceDiagnostics />

      {inspectorView === 'area' && mapAreaTarget && project.plan ? <Dialog open={!pendingZone} title={mapAreaTarget.label} onClose={() => setMapAreaTarget(undefined)}><div className="module-form"><EditorPanel title={mapAreaTarget.label}><p>{mapAreaTarget.detail}</p><p className="editor-panel__hint">{mapAreaTarget.plantingZoneId ? 'Участок проекта' : 'Объект исходного DXF'}</p>{mapAreaTarget.plantingZoneId ? <EditorActions><Button variant="primary" onClick={() => activateTool('pattern_fill')}>Разместить здесь</Button></EditorActions> : mapAreaTarget.selectable && mapAreaTarget.geometry ? <EditorActions><Button variant="primary" disabled={planLocked || editorBusy} onClick={() => { if (!mapAreaTarget.geometry) return; const zone = assignmentFromGeometry(mapAreaTarget.geometry, (project.planting_zones?.length ?? 0) + 1, mapAreaTarget.label, mapAreaTarget.sourceId); setPendingZone({ zone, purpose: 'place' }); setZoneReviewOpen(true); setMapAreaTarget(undefined); }}>Сделать рабочим участком</Button></EditorActions> : null}</EditorPanel></div></Dialog> : null}
      {pendingZone ? <Dialog open={zoneReviewOpen} title={zoneReviewQuery.data?.can_save ? 'Сохранить рабочий участок?' : 'Проверка границ участка'} onClose={() => setZoneReviewOpen(false)} footer={<><Button variant="secondary" disabled={editorBusy} onClick={() => { const existing = project.planting_zones?.some(zone => zone.id === pendingZone.zone.id); setZoneDrawingMode(existing ? pendingZone.zone.id : 'new'); setPendingZone(undefined); setZoneReviewOpen(false); setPanel('zones'); editor.setTool('draw_area'); }}>Перерисовать</Button><Button variant="secondary" onClick={() => { setPendingZone(undefined); setZoneReviewOpen(false); }}>Отменить</Button><Button variant="primary" disabled={!zoneReviewQuery.data?.can_save || zoneReviewQuery.isFetching} loading={saveManagedZones.isPending || savePlacementZone.isPending} onClick={() => { if (pendingZone.purpose === 'place') savePlacementZone.mutate({ zone: pendingZone.zone }); else saveManagedZones.mutate({ zones: [...(project.planting_zones ?? []).filter(zone => zone.id !== pendingZone.zone.id), pendingZone.zone], focusId: pendingZone.zone.id }); }}>Сохранить участок</Button></>}><ZoneReview zone={pendingZone.zone} zones={project.planting_zones ?? []} preview={zoneReviewQuery.data} />{zoneReviewQuery.error ? <><p role="alert">{message(zoneReviewQuery.error)}</p><Button variant="secondary" onClick={() => void zoneReviewQuery.refetch()}>Повторить проверку</Button></> : null}</Dialog> : null}
      <Dialog open={libraryOpen} title="Посадки проекта" size="wide" stableHeight onClose={() => setLibraryOpen(false)}><PlantingLibrary objects={planObjects} zones={project.planting_zones ?? []} names={speciesNames} initialIds={selectedIds} onSelect={ids => { selectFromExplorer(ids, false); mapViewport.current?.fitObjects(ids); setLibraryOpen(false); }} onSpecies={ids => { selectFromExplorer(ids, false); setSpeciesAssignmentOpen(true); setLibraryOpen(false); }} /></Dialog>
      {project.plan && panel === 'zones' ? <Dialog open={!zoneDrawingMode && !zonePendingDelete && !pendingZone} title="Рабочие участки" size="wide" stableHeight keepMounted onClose={() => setPanel(null)}><PlantingZoneManager zones={draftZones} activeIds={selectedPatternZoneIds} onSelectionChange={selectPatternZones} zoneUsage={plantingZoneUsage} drawing={Boolean(zoneDrawingMode)} saving={saveManagedZones.isPending} error={saveManagedZones.error ? message(saveManagedZones.error) : undefined} onFocus={(zone) => { focusZones([zone]); setPanel(null); }} onRename={(zone, label) => { if (label === zone.label) return; const zones = draftZones.map((item) => item.id === zone.id ? { ...item, label } : item); setDraftZones(zones); saveManagedZones.mutate({ zones, focusId: zone.id }); }} onRedraw={(zone) => { if (sceneOpen) changeMapMode('2d'); if (!zone.id) return; setSelectedPatternZoneIds([zone.id]); setZoneDrawingMode(zone.id); editor.setTool('draw_area'); }} onDelete={setZonePendingDelete} onDraw={() => { if (sceneOpen) changeMapMode('2d'); setZoneDrawingMode('new'); editor.setTool('draw_area'); }} onCancelDraw={() => { setZoneDrawingMode(undefined); editor.setTool('select'); }} /></Dialog> : null}
      {selectedObjects.length ? <Dialog open={speciesAssignmentOpen && !reviewOpen} title="Назначить породу" size={speciesCatalogBrowsing ? "wide" : "form"} keepMounted onClose={() => setSpeciesAssignmentOpen(false)}><div className="module-form"><SpeciesAssignmentPanel onCatalogModeChange={setSpeciesCatalogBrowsing} onSelectKind={(kind) => editor.select(selectedObjects.flatMap(object => object.kind === kind && object.id ? [object.id] : []), 'replace')} objects={selectedObjects} shortlist={speciesShortlistQuery.data} loading={speciesShortlistQuery.isLoading} previewing={previewChanges.isPending} error={speciesShortlistQuery.error ? message(speciesShortlistQuery.error) : undefined} onAssign={previewSpeciesAssignment} onCancel={() => setSpeciesAssignmentOpen(false)} /></div></Dialog> : null}
      {(tool === 'pattern_fill' || tool === 'draw_area' && placementAreaDrawing || pendingZone?.purpose === 'place') && project.plan ? <PlacementWorkspace busy={busy} open={patternSettingsOpen && !placementAreaDrawing && !pendingZone} hasPreview={Boolean(patternPreview || recommendationPreview)} recommendation={recommendationOpen} onRecommendation={setRecommendationOpen} onClose={() => setPatternSettingsOpen(false)} manual={patternTool} automatic={<>
        <div className="placement-recommendation" hidden={Boolean(recommendationPreview)}>
          <RecommendationPanel onChooseComposition={() => setRecommendationOpen(false)} calculating={previewRecommendation.isPending} onCancelCalculation={() => { recommendationAbortRef.current?.abort(); previewRecommendation.reset(); }} guided selectedZoneIds={selectedPatternZoneIds} onSelectedZoneIdsChange={selectPatternZones} zones={project.planting_zones ?? []} loading={previewRecommendation.isPending}
            active={recommendationOpen} onScreenMode={setBuildingScreenActive} screenTargets={buildingTargets.data} screenLoading={buildingTargets.isFetching} screenError={buildingTargets.error ? message(buildingTargets.error) : undefined}
            onScreenPreview={draft => { if (sceneOpen) changeMapMode('2d'); previewRecommendation.mutate({ ...draft, base_plan_version: project.plan!.version }); }}
            onPreview={draft => { if (sceneOpen) changeMapMode('2d'); previewRecommendation.mutate({ ...draft, base_plan_version: project.plan!.version }); }}
            onCancel={() => { recommendationAbortRef.current?.abort(); setRecommendationOpen(false); setRecommendationPreview(undefined); editor.setTool('select'); }} />
        </div>
        {recommendationPreview ? <RecommendationReviewPanel requestedCount={previewRecommendation.variables?.max_sites} speciesNames={speciesNames} proposal={recommendationPreview} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} applying={applyChanges.isPending} onApply={() => { if (recommendationPreview.change_set?.can_apply) applyChanges.mutate(recommendationPreview.change_set); }} onCancel={() => { setRecommendationPreview(undefined); editor.setPreview(undefined); }} /> : null}
      </>} /> : null}
      <Dialog open={singleSettingsOpen && (tool === 'add_tree' || tool === 'add_shrub')} title={tool === 'add_shrub' ? 'Новый кустарник' : 'Новое дерево'} onClose={() => setSingleSettingsOpen(false)} footer={<Button variant="primary" disabled={!(tool === 'add_tree' ? singleTreeSpecies : singleShrubSpecies)} onClick={() => setSingleSettingsOpen(false)}>Указать место на карте</Button>}><SpeciesPicker species={(speciesQuery.data ?? []).filter(item => item.kind === (tool === 'add_shrub' ? 'shrub' : 'tree'))} value={tool === 'add_shrub' ? singleShrubSpecies : singleTreeSpecies} onChange={tool === 'add_shrub' ? setSingleShrubSpecies : setSingleTreeSpecies} /></Dialog>

      {changePreview ? <ChangeSetReviewPanel open={reviewOpen} preview={changePreview} applying={applyChanges.isPending} error={applyChanges.error ? message(applyChanges.error) : undefined} unverifiedData={patternPreview?.unverified_data ?? recommendationPreview?.data_gaps ?? []} onInspect={() => { if (!applyChanges.isPending) setReviewOpen(false); }} onCancel={() => { if (applyChanges.isPending) return; setReviewOpen(false); editor.setPreview(undefined); setPatternPreview(undefined); setBrushPreview(undefined); setBrushStrokes([]); setRecommendationPreview(undefined); applyChanges.reset(); }} onApply={() => applyChanges.mutate(changePreview)} /> : null}
      <Dialog open={Boolean(operationError && operationError !== dismissedOperationError && !reviewOpen && !deleteSelectionOpen && !zonePendingDelete)} title={isProjectConflict(operationError) ? 'План изменился' : 'Не удалось выполнить действие'} onClose={() => setDismissedOperationError(operationError)} footer={<Button variant="primary" onClick={() => setDismissedOperationError(operationError)}>Вернуться к работе</Button>}>{operationError ? isProjectConflict(operationError) ? <ProjectConflictNotice error={operationError} onReload={() => { setDismissedOperationError(operationError); void reloadAfterConflict(); }} reloading={projectQuery.isFetching} /> : <p>{message(operationError)}</p> : null}</Dialog>
      <Dialog open={releaseOpen} title="Выпуск проекта" onClose={() => setReleaseOpen(false)}>{project.plan ? <ReleasePanel plan={project.plan} release={release} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} loading={createRelease.isPending} error={createRelease.error ? message(createRelease.error) : undefined} onCreate={(request) => createRelease.mutate(request)} onDownload={(path) => { window.location.href = api.downloadUrl(path); }} /> : null}</Dialog>
      <Dialog open={navigationBlocker.state === 'blocked'} title={savingPlan ? 'Дождитесь сохранения' : 'Уйти без применения?'} onClose={() => navigationBlocker.reset?.()} footer={<><Button variant="secondary" onClick={() => navigationBlocker.reset?.()}>Остаться</Button>{!savingPlan ? <Button variant="primary" onClick={() => navigationBlocker.proceed?.()}>Уйти без применения</Button> : null}</>}><p>{savingPlan ? 'Изменения ещё сохраняются. Дождитесь завершения перед выходом из плана.' : 'Предпросмотр и незавершённое рисование не сохранятся. Уже применённые посадки останутся в плане.'}</p></Dialog>
      <Dialog open={pendingScene} title="Сначала завершите расстановку" onClose={() => setPendingScene(false)} footer={<Button variant="primary" onClick={() => setPendingScene(false)}>Вернуться к расстановке</Button>}><p>В 3D показан сохранённый план. Примените или отмените текущий предпросмотр, чтобы перейти к нему.</p></Dialog>
      <Dialog open={Boolean(pendingTool)} title="Отменить текущий предпросмотр?" onClose={() => setPendingTool(undefined)} footer={<><Button variant="secondary" onClick={() => setPendingTool(undefined)}>Остаться</Button><Button variant="primary" onClick={() => { if (!pendingTool) return; assistant.discard(); patternAbortRef.current?.abort(); recommendationAbortRef.current?.abort(); brushAbortRef.current?.abort(); setPatternPreview(undefined); setRecommendationPreview(undefined); setBrushPreview(undefined); setBrushStrokes([]); setRowAxis(undefined); setRowAxisSource(undefined); editor.setPreview(undefined); setSpeciesAssignmentOpen(false); setRecommendationOpen(false); setPanel(null); setActiveLayerId(undefined); editor.setTool(pendingTool); if (!selectedPatternZoneIds.length && plantingZoneIds.length === 1) setSelectedPatternZoneIds(plantingZoneIds); setPendingTool(undefined); }}>Отменить и перейти</Button></>}><p>Посадки ещё не изменены. Новый инструмент начнёт отдельную операцию.</p></Dialog>
      <Dialog open={deleteSelectionOpen} title={selectedIds.length > 1 ? `Удалить ${plantingCount(selectedIds.length)}` : 'Удалить посадку'} onClose={() => setDeleteSelectionOpen(false)} footer={<><Button variant="secondary" disabled={editorBusy} onClick={() => setDeleteSelectionOpen(false)}>Отмена</Button><Button variant="danger" icon={Trash2} loading={deleteObjects.isPending} disabled={createRelease.isPending} onClick={() => deleteObjects.mutate(selectedIds, { onSuccess: () => setDeleteSelectionOpen(false) })}>Удалить</Button></>}><p>Посадки исчезнут из текущей схемы</p>{deleteObjects.error ? <p role="alert">{message(deleteObjects.error)}</p> : null}</Dialog>
      <Dialog open={Boolean(zonePendingDelete)} title="Удалить рабочий участок" onClose={() => setZonePendingDelete(undefined)} footer={<><Button variant="secondary" disabled={saveManagedZones.isPending} onClick={() => setZonePendingDelete(undefined)}>Отмена</Button><Button variant="danger" icon={Trash2} loading={saveManagedZones.isPending} onClick={() => { if (!zonePendingDelete) return; saveManagedZones.mutate({ zones: draftZones.filter((zone) => zone.id !== zonePendingDelete.id) }); }}>Удалить</Button></>}><p>Участок можно удалить, если в нём ещё нет сохранённых посадок</p>{saveManagedZones.error ? <p role="alert">{message(saveManagedZones.error)}</p> : null}</Dialog>
    </main>
  </div>;
}
