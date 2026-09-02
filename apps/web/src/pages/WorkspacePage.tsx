import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiClientError, type BrushPreview, type BrushPreviewRequest, type BrushStroke, type ChangeSetPreview, type Layer, type PatternPreview, type PatternPreviewRequest, type PlacementCheck, type PlanChangeSetDraft, type PlanObject, type PlantingZoneAssignment, type RecommendationPreview, type RecommendationRequest, type ReleaseCreateRequest, type ReleasePackage } from '@green/api-client';
import { AlertTriangle, ChevronLeft, ChevronRight, Crosshair, Layers3, Maximize2, Minus, PanelRightClose, Plus, Scan, Trash2 } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Dialog, IconButton, InlineMessage, Progress } from '@green/ui';
import { AppHeader } from '../domain-ui/AppHeader';
import { ReleasePanel } from '../domain-ui/ReleasePanel';
import { ChangeSetReviewPanel } from '../domain-ui/ChangeSetReviewPanel';
import { BrushToolPanel } from '../domain-ui/BrushToolPanel';
import { GroupInspector } from '../domain-ui/GroupInspector';
import { groupTransformDraft } from '../domain-ui/groupTransform';
import { HistoryPanel } from '../domain-ui/HistoryPanel';
import { InspectorHeader } from '../domain-ui/InspectorHeader';
import type { GrowthHorizon } from '../domain-ui/GrowthHorizonControl';
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
import { RecommendationReviewPanel } from '../domain-ui/RecommendationReviewPanel';
import { SpeciesAssignmentPanel } from '../domain-ui/SpeciesAssignmentPanel';
import { ValidationPanel } from '../domain-ui/ValidationPanel';
import { WorkspaceNavigation, type WorkspaceDestination } from '../domain-ui/WorkspaceNavigation';
import { useWorkspaceEditor } from '../features/workspace/useWorkspaceEditor';
import type { SelectionMode } from '../domain-ui/selection';
import { plantingCount } from '../domain-ui/countLabel';

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
  const mapRequestRef = useRef<BufferedMapRequest | undefined>(undefined);
  const mapRequestTimerRef = useRef<number | undefined>(undefined);
  const placementTimerRef = useRef<number | undefined>(undefined);
  const placementAbortRef = useRef<AbortController | undefined>(undefined);
  const placementRequestRef = useRef(0);
  const patternAbortRef = useRef<AbortController | undefined>(undefined);
  const recommendationAbortRef = useRef<AbortController | undefined>(undefined);
  const brushAbortRef = useRef<AbortController | undefined>(undefined);
  const movePreviewTimerRef = useRef<number | undefined>(undefined);
  const movePreviewAbortRef = useRef<AbortController | undefined>(undefined);
  const movePreviewRequestRef = useRef(0);
  const mapEditInFlightRef = useRef(false);
  const initializedProjectRef = useRef<string | undefined>(undefined);

  const [panel, setPanel] = useState<WorkspacePanel>(null);
  const [leftOpen, setLeftOpen] = useState(false);
  // The current task is the inspector. On a compact desktop it stays open
  // until the user explicitly collapses it; otherwise the first actionable
  // step is hidden behind an unexplained map-only screen.
  const [rightOpen, setRightOpen] = useState(true);
  const [activeLayerId, setActiveLayerId] = useState<string>();
  const [visibility, setVisibility] = useState<Record<string, boolean>>({});
  const [deleteSelectionOpen, setDeleteSelectionOpen] = useState(false);
  const editor = useWorkspaceEditor(projectId);
  const { tool, selectedIds, preview: changePreview } = editor;
  const [cursor, setCursor] = useState<[number, number]>();
  const [mapHoverTarget, setMapHoverTarget] = useState<MapHoverTarget>();
  const [mapAreaTarget, setMapAreaTarget] = useState<MapAreaTarget>();
  const [placementCheck, setPlacementCheck] = useState<PlacementCheck>();
  const [moveLiveCheck, setMoveLiveCheck] = useState<{ status: 'checking' | 'allowed' | 'blocked' | 'unknown'; reason: string }>();
  const [mapRequest, setMapRequest] = useState<BufferedMapRequest>();
  const [draftZones, setDraftZones] = useState<PlantingZoneAssignment[]>([]);
  const [zoneDrawingMode, setZoneDrawingMode] = useState<'new' | string>();
  const [zonePendingDelete, setZonePendingDelete] = useState<PlantingZoneAssignment>();
  const [release, setRelease] = useState<ReleasePackage>();
  const [rowAxis, setRowAxis] = useState<{ type: 'LineString'; coordinates: number[][] }>();
  const [rowAxisSource, setRowAxisSource] = useState<{ type: 'dxf' | 'manual'; label: string }>();
  const [patternPreview, setPatternPreview] = useState<PatternPreview>();
  const [selectedPatternZoneIds, setSelectedPatternZoneIds] = useState<string[]>([]);
  const [placementAreaDrawing, setPlacementAreaDrawing] = useState(false);
  const [recommendationOpen, setRecommendationOpen] = useState(false);
  const [recommendationPreview, setRecommendationPreview] = useState<RecommendationPreview>();
  const [brushStrokes, setBrushStrokes] = useState<BrushStroke[]>([]);
  const [brushPreview, setBrushPreview] = useState<BrushPreview>();
  const [brushWidth, setBrushWidth] = useState(12);
  const [brushOperation, setBrushOperation] = useState<'add' | 'subtract'>('add');
  const [speciesAssignmentOpen, setSpeciesAssignmentOpen] = useState(false);
  const [growthHorizon, setGrowthHorizon] = useState<GrowthHorizon>(0);
  const [sceneOpen, setSceneOpen] = useState(false);
  const [sceneHorizon, setSceneHorizon] = useState<number>(0);

  const projectQuery = useQuery({ queryKey: ['workspace-project', projectId], queryFn: () => api.getProject(projectId, false), enabled: Boolean(projectId) });
  const project = projectQuery.data;
  const layers = project?.layers ?? EMPTY_LAYERS;
  const planObjects = project?.plan?.objects ?? EMPTY_PLAN_OBJECTS;
  const sourceWarnings = project?.source_file?.warnings ?? [];
  const projectHasPlan = Boolean(project?.plan);
  // A DXF may legitimately have no outer site contour. Once geometry is
  // prepared it is still a working map: the operator draws the local area
  // instead of getting trapped in a read-only preview.
  const sourcePreview = Boolean(project && !project.map_ready);
  // A draft stays directly editable until the operator exports it for the
  // next process.
  const planLocked = false;
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
  const sceneQuery = useQuery({ queryKey: ['plan-scene', projectId, project?.plan?.version, sceneHorizon], queryFn: () => api.getPlanScene(projectId, sceneHorizon), enabled: Boolean(sceneOpen && project?.plan), staleTime: Number.POSITIVE_INFINITY });

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
    setLeftOpen(false);
    setRightOpen(true);
    setActiveLayerId(undefined);
    setVisibility({});
    setDeleteSelectionOpen(false);
    setCursor(undefined);
    setMapHoverTarget(undefined);
    setPlacementCheck(undefined);
    setMoveLiveCheck(undefined);
    setMapRequest(undefined);
    setDraftZones([]);
    setZoneDrawingMode(undefined);
    setZonePendingDelete(undefined);
    setRelease(undefined);
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
    setGrowthHorizon(0);
    setSceneOpen(false);
    setSceneHorizon(0);
  }, [projectId]);

  useEffect(() => {
    if (!project || initializedProjectRef.current === project.id) return;
    initializedProjectRef.current = project.id;
    const projectZones = project.planting_zones ?? [];
    setDraftZones(projectZones);
    setSelectedPatternZoneIds([]);
    setPanel(project.plan ? null : 'zones');
  }, [project]);

  useEffect(() => () => {
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
  const selectedPatternZone = useMemo(() => (project?.planting_zones ?? []).find((zone) => zone.id === selectedPatternZoneIds[0]), [project?.planting_zones, selectedPatternZoneIds]);
  const plantingZoneIds = useMemo(() => (project?.planting_zones ?? []).flatMap((zone) => zone.id ? [zone.id] : []), [project?.planting_zones]);
  const selectPatternZones = useCallback((ids: string[]) => {
    setSelectedPatternZoneIds(ids);
    if (ids.length !== 1) return;
    const zone = (project?.planting_zones ?? []).find((item) => item.id === ids[0]);
    if (zone) mapViewport.current?.fitGeometry(zone.geometry);
  }, [project?.planting_zones]);
  const plantingZoneUsage = useMemo(() => planObjects.reduce<Record<string, number>>((usage, object) => {
    if (object.planting_zone_id) usage[object.planting_zone_id] = (usage[object.planting_zone_id] ?? 0) + 1;
    return usage;
  }, {}), [planObjects]);
  const selectedObjects = useMemo(() => {
    const ids = new Set(selectedIds);
    return planObjects.filter((item) => item.id && ids.has(item.id));
  }, [planObjects, selectedIds]);
  const selectedIdsKey = [...selectedIds].sort().join(':');
  const speciesShortlistQuery = useQuery({ queryKey: ['species-shortlist', projectId, selectedIdsKey], queryFn: () => api.shortlistSpecies(projectId, selectedIds), enabled: Boolean(speciesAssignmentOpen && selectedIds.length), staleTime: 30_000 });
  const selectedPatternZonesKey = [...selectedPatternZoneIds].sort().join(',');
  const zoneSpeciesShortlistQuery = useQuery({ queryKey: ['species-shortlist-zones', projectId, selectedPatternZonesKey], queryFn: () => api.shortlistSpecies(projectId, { zoneIds: selectedPatternZoneIds }), enabled: Boolean(project?.plan && selectedPatternZoneIds.length && (tool === 'pattern_fill' || tool === 'pattern_row')), staleTime: 30_000 });
  const speciesNames = useMemo(() => new Map((speciesQuery.data ?? []).map((item) => [item.id, item.common_name])), [speciesQuery.data]);
  const issues = project?.plan?.issues ?? [];
  const issueCount = issues.length;
  const placementPreview = useMemo(() => {
    if (!cursor || (tool !== 'add_tree' && tool !== 'add_shrub')) return undefined;
    const kind = tool === 'add_tree' ? 'tree' : 'shrub';
    const check = placementCheck && Math.abs(placementCheck.x - cursor[0]) < 0.01 && Math.abs(placementCheck.y - cursor[1]) < 0.01 ? placementCheck : undefined;
    return { coordinate: cursor, radius: kind === 'tree' ? 1.6 : 0.65, status: check?.status ?? 'unknown' } as const;
  }, [cursor, placementCheck, tool]);

  const openLeftPanel = useCallback(() => setLeftOpen(true), []);
  const openRightPanel = useCallback(() => { setLeftOpen(false); setRightOpen(true); }, []);
  const closeRightPanel = useCallback(() => setRightOpen(false), []);
  const activeWorkspaceDestination: WorkspaceDestination = panel === 'zones' ? 'zones' : panel === 'issues' ? 'issues' : 'plantings';
  const navigateWorkspace = useCallback((destination: WorkspaceDestination) => {
    setActiveLayerId(undefined);
    setMapAreaTarget(undefined);
    editor.clearSelection();
    editor.setTool('select');
    setPanel(destination);
    if (destination === 'zones') setDraftZones(project?.planting_zones ?? []);
    openRightPanel();
  }, [editor, openRightPanel, project?.planting_zones]);

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
  const savePlacementZone = useMutation({
    mutationFn: (zone: PlantingZoneAssignment) => api.savePlantingZones(projectId, [...(project?.planting_zones ?? []), zone]),
    onSuccess: async (nextProject, zone) => {
      queryClient.setQueryData(['workspace-project', projectId], nextProject);
      setDraftZones(nextProject.planting_zones ?? []);
      setSelectedPatternZoneIds(zone.id ? [zone.id] : []);
      setPlacementAreaDrawing(false);
      editor.setTool('pattern_fill');
      await refresh({ mapGeometry: true });
      openRightPanel();
    },
  });
  const saveManagedZones = useMutation({
    mutationFn: ({ zones }: { zones: PlantingZoneAssignment[]; focusId?: string }) => api.savePlantingZones(projectId, zones),
    onSuccess: async (nextProject, variables) => {
      queryClient.setQueryData(['workspace-project', projectId], nextProject);
      setDraftZones(nextProject.planting_zones ?? []);
      setSelectedPatternZoneIds(variables.focusId ? [variables.focusId] : []);
      setZoneDrawingMode(undefined);
      setZonePendingDelete(undefined);
      editor.setTool('select');
      await refresh({ mapGeometry: true });
    },
  });
  const addObject = useMutation({ mutationFn: ({ kind, coordinate }: { kind: 'tree' | 'shrub'; coordinate: [number, number] }) => api.addPlanObject(projectId, { kind, x: coordinate[0], y: coordinate[1] }), onSuccess: async () => { await refresh(); } });
  const previewChanges = useMutation({ mutationFn: (draft: PlanChangeSetDraft) => api.previewPlanChanges(projectId, draft), onSuccess: (preview) => { editor.setTool('select'); editor.setPreview(preview); setActiveLayerId(undefined); setPanel(null); openRightPanel(); } });
  const previewPattern = useMutation({
    mutationFn: (request: PatternPreviewRequest) => {
      patternAbortRef.current?.abort();
      const controller = new AbortController();
      patternAbortRef.current = controller;
      return api.previewPlanPattern(projectId, request, controller.signal);
    },
    onSuccess: (result) => {
      setPatternPreview(result);
      editor.setPreview(result.change_set ?? undefined);
      setActiveLayerId(undefined);
      setPanel(null);
      openRightPanel();
    },
  });
  const previewRecommendation = useMutation({
    mutationFn: (request: RecommendationRequest) => {
      recommendationAbortRef.current?.abort();
      const controller = new AbortController();
      recommendationAbortRef.current = controller;
      return api.previewRecommendation(projectId, request, controller.signal);
    },
    onSuccess: (result) => {
      setRecommendationPreview(result);
      editor.setPreview(result.change_set ?? undefined);
      setActiveLayerId(undefined);
      setPanel(null);
      openRightPanel();
    },
  });
  const previewBrush = useMutation({
    mutationFn: (request: BrushPreviewRequest) => {
      brushAbortRef.current?.abort();
      const controller = new AbortController();
      brushAbortRef.current = controller;
      return api.previewBrush(projectId, request, controller.signal);
    },
    onSuccess: (result) => {
      setBrushPreview(result);
      editor.setPreview(result.change_set ?? undefined);
      setActiveLayerId(undefined);
      setPanel(null);
      openRightPanel();
    },
  });
  const applyChanges = useMutation({ mutationFn: (explicitPreview?: ChangeSetPreview) => {
    const preview = explicitPreview ?? changePreview;
    if (!preview) throw new Error('Предпросмотр изменений недоступен');
    return api.applyPlanChanges(projectId, preview);
  }, onSuccess: async (result) => { setPatternPreview(undefined); setRecommendationOpen(false); setRecommendationPreview(undefined); setBrushStrokes([]); setBrushPreview(undefined); setRowAxis(undefined); setRowAxisSource(undefined); setSpeciesAssignmentOpen(false); editor.setPreview(undefined); editor.setTool('select'); editor.select([...(result.added_ids ?? []), ...(result.updated_ids ?? [])], 'replace'); await refresh(); } });
  const deleteObjects = useMutation({ mutationFn: (ids: string[]) => api.deletePlanObjects(projectId, ids), onSuccess: async () => { editor.clearSelection(); await refresh(); } });
  const undoChange = useMutation({ mutationFn: () => api.undoPlanChange(projectId), onSuccess: async () => { editor.clearSelection(); editor.setTool('select'); await refresh(); } });
  const redoChange = useMutation({ mutationFn: () => api.redoPlanChange(projectId), onSuccess: async () => { editor.clearSelection(); editor.setTool('select'); await refresh(); } });
  const createRelease = useMutation({ mutationFn: (request: ReleaseCreateRequest) => api.createRelease(projectId, request), onSuccess: setRelease });
  const busy = addObject.isPending || savePlacementZone.isPending || saveManagedZones.isPending || previewChanges.isPending || previewPattern.isPending || previewRecommendation.isPending || previewBrush.isPending || applyChanges.isPending || deleteObjects.isPending || undoChange.isPending || redoChange.isPending;
  // Export captures one durable version of the plan. Do not let a normal map
  // click race that snapshot and surface an avoidable version conflict.
  const editorBusy = busy || createRelease.isPending;

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
    if (!projectHasPlan) {
      if (target.selectable && target.geometry) addMapArea(target.geometry, target.label, target.sourceId);
      return;
    }
    if (panel === 'zones') {
      if (target.plantingZoneId) setSelectedPatternZoneIds([target.plantingZoneId]);
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
  }, [addMapArea, editor, openRightPanel, panel, projectHasPlan, tool]);

  const handleMapStackSelect = useCallback((item: MapHoverItem) => {
    handleMapArea(item.target);
  }, [handleMapArea]);

  const activateTool = useCallback((nextTool: MapTool) => {
    if (editorBusy) return;
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
      if (mapAreaTarget?.plantingZoneId) setSelectedPatternZoneIds([mapAreaTarget.plantingZoneId]);
      else if (!selectedPatternZoneIds.length && plantingZoneIds.length === 1) setSelectedPatternZoneIds(plantingZoneIds);
    }
    editor.setTool(tool === nextTool && nextTool !== 'select' ? 'select' : nextTool);
  }, [editor, editorBusy, mapAreaTarget?.plantingZoneId, openRightPanel, planLocked, plantingZoneIds, projectHasPlan, selectedPatternZoneIds.length, sourcePreview, tool]);

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
      if (movePreviewTimerRef.current !== undefined) window.clearTimeout(movePreviewTimerRef.current);
      movePreviewTimerRef.current = undefined;
      movePreviewAbortRef.current?.abort();
      setMoveLiveCheck(undefined);
      return;
    }
    const draft = groupTransformDraft(plan.version, selectedObjects, 'move', coordinate);
    if (!draft) return;
    setMoveLiveCheck({ status: 'checking', reason: 'Проверяем новое положение' });
    if (movePreviewTimerRef.current !== undefined) window.clearTimeout(movePreviewTimerRef.current);
    movePreviewTimerRef.current = window.setTimeout(() => {
      movePreviewAbortRef.current?.abort();
      const controller = new AbortController();
      movePreviewAbortRef.current = controller;
      const requestId = movePreviewRequestRef.current + 1;
      movePreviewRequestRef.current = requestId;
      void api.previewPlanChanges(projectId, draft, controller.signal).then((preview) => {
        if (requestId !== movePreviewRequestRef.current) return;
        const results = preview.candidate_results ?? [];
        const issue = results.find((item) => item.status === 'blocked')
          ?? results.find((item) => item.status !== 'allowed');
        setMoveLiveCheck(preview.can_apply
          ? { status: 'allowed', reason: 'Можно переместить' }
          : { status: issue?.status === 'blocked' ? 'blocked' : 'unknown', reason: issue?.reason ?? 'Положение требует проверки' });
      }).catch((error) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (requestId === movePreviewRequestRef.current) setMoveLiveCheck({ status: 'unknown', reason: 'Не удалось проверить положение' });
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
      if (!checkMatches || !placementCheck.allowed || mapEditInFlightRef.current) return;
      mapEditInFlightRef.current = true;
      addObject.mutate(
        { kind: tool === 'add_tree' ? 'tree' : 'shrub', coordinate },
        { onSettled: () => { mapEditInFlightRef.current = false; } },
      );
    }
    if ((tool === 'move' || tool === 'copy' || tool === 'select') && selectedObjects.length && !mapEditInFlightRef.current) {
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
      if (target?.matches('input, textarea, select, [contenteditable="true"]')) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        if (sceneOpen) {
          setSceneOpen(false);
          return;
        }
        const hasActiveOperation = tool !== 'select'
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
        applyChanges.mutate(changePreview);
        return;
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedIds.length && !editorBusy) {
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
  }, [applyChanges, brushStrokes.length, changePreview, editor, editorBusy, historyQuery.data?.can_redo, historyQuery.data?.can_undo, patternPreview, planLocked, recommendationOpen, recommendationPreview, redoChange, sceneOpen, selectedIds.length, speciesAssignmentOpen, tool, undoChange]);

  if (projectQuery.isLoading) return <div className="app-shell"><AppHeader /><main className="center-status"><Progress label="Загрузка рабочей области" /></main></div>;
  if (!project) return <div className="app-shell"><AppHeader /><main className="center-status"><InlineMessage tone="error">{message(projectQuery.error)}</InlineMessage></main></div>;

  const operationError = createManualPlan.error ?? savePlacementZone.error ?? saveManagedZones.error ?? addObject.error ?? previewChanges.error ?? previewPattern.error ?? previewRecommendation.error ?? previewBrush.error ?? applyChanges.error ?? deleteObjects.error ?? undoChange.error ?? redoChange.error ?? mapGeometryQuery.error;
  const showMapStatus = Boolean(
    (placementCheck && (tool === 'add_tree' || tool === 'add_shrub'))
    || (moveLiveCheck && (tool === 'move' || tool === 'select'))
    || mapGeometryQuery.isFetching
    || mapGeometryMetadata?.truncated,
  );
  const showPlantingsOverview = Boolean(project.plan && (
    panel === 'plantings'
    || (panel === null
      && !activeLayer
      && !mapAreaTarget
      && !changePreview
      && !recommendationOpen
      && !speciesAssignmentOpen
      && !selectedIds.length
      && tool !== 'pattern_row'
      && tool !== 'pattern_fill'
      && tool !== 'brush')
  ));

  return <div className="app-shell workspace-screen">
    <AppHeader workspace projectName={project.name} onReview={project.plan ? () => { setPanel('issues'); openRightPanel(); } : undefined} onHistory={project.plan ? () => { setPanel('history'); openRightPanel(); } : undefined} onExport={project.plan ? () => { setPanel('export'); openRightPanel(); } : undefined} exporting={createRelease.isPending} onUndo={!planLocked && historyQuery.data?.can_undo ? () => undoChange.mutate() : undefined} onRedo={!planLocked && historyQuery.data?.can_redo ? () => redoChange.mutate() : undefined} undoLabel={historyQuery.data?.undo_label} redoLabel={historyQuery.data?.redo_label} historyBusy={undoChange.isPending || redoChange.isPending} actionsDisabled={editorBusy || createManualPlan.isPending} />
    <main className={`workspace-layout ${leftOpen ? '' : 'is-left-collapsed'} ${rightOpen ? '' : 'is-right-collapsed'}`}>
      <aside className="workspace-left"><ProjectLayers layers={layers} visibility={visibility} activeLayerId={activeLayerId} onVisibility={(id, visible) => setVisibility((current) => ({ ...current, [id]: visible }))} onSelect={(id) => { setActiveLayerId((current) => current === id ? undefined : id); editor.clearSelection(); setPanel(null); openRightPanel(); }} onClose={() => setLeftOpen(false)} /></aside>
      <aside className="workspace-left-collapsed"><IconButton icon={ChevronRight} label="Развернуть слои" variant="ghost" onClick={openLeftPanel} /><IconButton icon={Layers3} label="Слои" active onClick={openLeftPanel} /></aside>
      <section className={`map-canvas ${rightOpen ? '' : 'has-right-dock'}`} onMouseLeave={() => setMapHoverTarget(undefined)}>
        <MapViewport key={projectId} ref={mapViewport} geometry={mapGeometryQuery.data?.feature_collection as Record<string, unknown> | undefined} geometryRevision={project.geometry_version} initialExtent={initialExtent} objects={planObjects} growthHorizon={growthHorizon} draftPlantingZones={!projectHasPlan || panel === 'zones' ? draftZones : undefined} hiddenLayerNames={hiddenLayerNames} selectedIds={selectedIds} highlightedPlantingZoneId={selectedPatternZoneIds.length === 1 ? selectedPatternZoneIds[0] : undefined} focusGeometry={selectedPatternZone?.geometry} placementPreview={placementPreview} changePreview={changePreview} tool={tool} brushWidthM={brushWidth} brushOperation={brushOperation} onDrawArea={(geometry) => {
          if (projectHasPlan && panel === 'zones' && zoneDrawingMode && !planLocked) {
            const previous = zoneDrawingMode === 'new' ? undefined : (project.planting_zones ?? []).find((zone) => zone.id === zoneDrawingMode);
            const zone = previous ? { ...previous, geometry } : assignmentFromGeometry(geometry, (project.planting_zones?.length ?? 0) + 1, `Участок ${(project.planting_zones?.length ?? 0) + 1}`);
            const zones = previous ? (project.planting_zones ?? []).map((item) => item.id === previous.id ? zone : item) : [...(project.planting_zones ?? []), zone];
            saveManagedZones.mutate({ zones, focusId: zone.id });
            return;
          }
          if (projectHasPlan && placementAreaDrawing && !planLocked) {
            const zone = assignmentFromGeometry(geometry, (project.planting_zones?.length ?? 0) + 1, `Участок ${project.planting_zones?.length ?? 0}`);
            savePlacementZone.mutate(zone);
            return;
          }
          if (projectHasPlan || planLocked) return;
          addMapArea(geometry, `Ручной участок ${draftZones.length + 1}`);
          editor.setTool('select');
        }} onDrawAxis={(geometry, source) => { setRowAxis(geometry); setRowAxisSource(source); setPatternPreview(undefined); editor.setPreview(undefined); openRightPanel(); }} onDrawBrush={(stroke) => { setBrushStrokes((current) => [...current, stroke]); setBrushPreview(undefined); editor.setPreview(undefined); openRightPanel(); }} onMapArea={handleMapArea} onMapHover={setMapHoverTarget} onSelect={(id, mode = 'replace') => {
          if (!projectHasPlan) return;
          if (!id) { if (mode === 'replace') editor.clearSelection(); return; }
          editor.select([id], mode);
          setMapHoverTarget(undefined);
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
        {!sceneOpen && !selectedObjects.length && mapHoverTarget && (tool === 'select' || tool === 'pattern_fill') ? <div className={`map-hover-hint ${mapHoverTarget.pixel[0] > 520 ? 'is-left' : ''}`} style={{ left: mapHoverTarget.pixel[0] + 14, top: mapHoverTarget.pixel[1] + 14 }} role="status" aria-label="Выбор объекта карты" onMouseDown={(event) => event.stopPropagation()}>{mapHoverTarget.items.map((item) => <button className={`map-hover-hint__item ${item.target.selectable ? 'is-selectable' : 'is-reference'}`} key={item.id} type="button" aria-label={`Выбрать ${item.label}`} onClick={(event) => { event.stopPropagation(); handleMapStackSelect(item); }}><strong>{item.label}</strong><span>{item.detail}</span></button>)}</div> : null}
        {!sceneOpen && projectHasPlan ? <div className="map-edit-tools"><MapToolbar tool={tool} onTool={activateTool} editable={!sourcePreview && !planLocked && !editorBusy} canDelete={selectedIds.length > 0} onDelete={() => setDeleteSelectionOpen(true)} /></div> : null}
        {!sceneOpen ? <div className="map-navigation-tools">
          <MapControlGroup className="map-zoom-tools" orientation="vertical" label="Масштаб карты"><IconButton icon={Plus} label="Увеличить" variant="ghost" onClick={() => mapViewport.current?.zoomIn()} /><IconButton icon={Minus} label="Уменьшить" variant="ghost" onClick={() => mapViewport.current?.zoomOut()} /><IconButton icon={Maximize2} label="Показать весь чертёж" variant="ghost" onClick={() => mapViewport.current?.fit()} /></MapControlGroup>
          {project.plan ? <MapControlGroup className="map-plan-focus" orientation="vertical" label="План озеленения"><IconButton icon={Crosshair} label="Показать посадки" variant="ghost" onClick={() => mapViewport.current?.fitPlan()} /></MapControlGroup> : null}
        </div> : null}
        {!rightOpen ? <div className="right-dock"><IconButton icon={ChevronLeft} label="Развернуть панель" variant="ghost" onClick={openRightPanel} /><IconButton icon={project.plan ? AlertTriangle : Scan} label={project.plan ? 'Проверка' : 'Участки'} variant="ghost" onClick={() => { setPanel(project.plan ? 'issues' : 'zones'); openRightPanel(); }} /></div> : null}
        {showMapStatus ? <div className="map-statusbar">{placementCheck && (tool === 'add_tree' || tool === 'add_shrub') ? <span className={`placement-check placement-check--${placementCheck.status}`} role="status">{placementCheck.reason}</span> : null}{moveLiveCheck && (tool === 'move' || tool === 'select') ? <span className={`placement-check placement-check--${moveLiveCheck.status}`} role="status">{moveLiveCheck.reason}</span> : null}{mapGeometryQuery.isFetching ? <span className="map-stream-status">Обновляем карту</span> : null}{mapGeometryMetadata?.truncated ? <span className="map-lod-warning" role="status">Приблизьте карту, чтобы увидеть детали</span> : null}</div> : null}
        {busy ? <div className="map-busy"><Progress label="Сохраняем изменения" /></div> : null}
        {operationError ? <div className="map-operation-error">{isProjectConflict(operationError) ? <ProjectConflictNotice error={operationError} onReload={() => void reloadAfterConflict()} reloading={projectQuery.isFetching} /> : <InlineMessage tone="error">{message(operationError)}</InlineMessage>}</div> : null}
        {sceneOpen ? <Suspense fallback={<div className="scene-review scene-review--loading"><Progress label="Загрузка 3D" /></div>}><SceneReview snapshot={sceneQuery.data} horizon={sceneHorizon} selectedIds={selectedIds} loading={sceneQuery.isLoading || sceneQuery.isFetching} error={sceneQuery.error ? message(sceneQuery.error) : undefined} onHorizon={setSceneHorizon} onSelect={(id) => editor.select([id], 'replace')} /></Suspense> : null}
        {project.plan ? <MapViewSwitch mode={sceneOpen ? '3d' : '2d'} onChange={(mode) => setSceneOpen(mode === '3d')} /> : null}
      </section>
      <aside className="workspace-right">
        <WorkspaceNavigation active={activeWorkspaceDestination} onChange={navigateWorkspace} issueCount={issueCount} hasPlan={projectHasPlan} />
        <div className="workspace-right__body">
        {!panel && !showPlantingsOverview ? <button className="right-close" type="button" aria-label="Свернуть инспектор" onClick={closeRightPanel}><PanelRightClose size={16} /></button> : null}
        {panel === 'zones' && !project.plan ? <div className="workspace-zones-panel"><button className="right-close" type="button" aria-label="Свернуть панель участков" onClick={closeRightPanel}><PanelRightClose size={16} /></button><PlantingZonesPanel assignments={draftZones} drawingManual={tool === 'draw_area'} saving={createManualPlan.isPending} error={createManualPlan.error ? message(createManualPlan.error) : undefined} onRemove={(id) => setDraftZones((current) => current.filter((item) => item.id !== id))} onSave={() => createManualPlan.mutate()} onManual={() => editor.setTool('draw_area')} onCancelManual={() => editor.setTool('select')} /></div> : null}
        {panel === 'zones' && project.plan ? <div className="rail-panel"><InspectorHeader title="Рабочие участки" meta={`${draftZones.length} на карте`} onClose={closeRightPanel} /><PlantingZoneManager zones={draftZones} activeId={selectedPatternZoneIds[0]} zoneUsage={plantingZoneUsage} drawing={Boolean(zoneDrawingMode)} saving={saveManagedZones.isPending} error={saveManagedZones.error ? message(saveManagedZones.error) : undefined} onFocus={(zone) => { if (zone.id) setSelectedPatternZoneIds([zone.id]); mapViewport.current?.fitGeometry(zone.geometry); }} onRename={(zone, label) => { if (label === zone.label) return; const zones = draftZones.map((item) => item.id === zone.id ? { ...item, label } : item); setDraftZones(zones); saveManagedZones.mutate({ zones, focusId: zone.id }); }} onRedraw={(zone) => { if (!zone.id) return; setSelectedPatternZoneIds([zone.id]); setZoneDrawingMode(zone.id); editor.setTool('draw_area'); }} onDelete={setZonePendingDelete} onDraw={() => { setZoneDrawingMode('new'); editor.setTool('draw_area'); }} onCancelDraw={() => { setZoneDrawingMode(undefined); editor.setTool('select'); }} /></div> : null}
        {panel === 'issues' ? <div className="rail-panel"><InspectorHeader title="Проверка плана" meta={issueCount ? `${issueCount} замечания` : 'Нарушений нет'} onClose={closeRightPanel} /><ValidationPanel issues={issues} onLocate={(id) => { if (id) editor.select([id], 'replace'); else editor.clearSelection(); editor.setTool('select'); setPanel(null); if (id) requestAnimationFrame(() => mapViewport.current?.fitSelection(id)); }} /></div> : null}
        {showPlantingsOverview ? <PlantingsOverviewPanel objects={planObjects} onPlace={() => activateTool('pattern_fill')} onFit={() => mapViewport.current?.fitPlan()} onClose={closeRightPanel} /> : null}
        {panel === 'history' ? <div className="rail-panel"><InspectorHeader title="История изменений" meta={`${historyQuery.data?.entries?.length ?? 0} ревизий`} onClose={closeRightPanel} /><HistoryPanel history={historyQuery.data} busy={undoChange.isPending || redoChange.isPending} onUndo={() => undoChange.mutate()} onRedo={() => redoChange.mutate()} /></div> : null}
        {panel === 'export' && project.plan ? <div className="rail-panel export-panel"><InspectorHeader title="Выпускной пакет" meta="Ревизия для дальнейшей работы" onClose={closeRightPanel} /><div className="rail-panel__content"><ReleasePanel plan={project.plan} release={release} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} loading={createRelease.isPending} error={createRelease.error ? message(createRelease.error) : undefined} onCreate={(request) => createRelease.mutate(request)} onDownload={(path) => { window.location.href = api.downloadUrl(path); }} /></div></div> : null}
        {!panel && activeLayer ? <LayerInspector layer={activeLayer} visible={visibility[activeLayer.id] !== false} onVisibility={(visible) => setVisibility((current) => ({ ...current, [activeLayer.id]: visible }))} onFit={() => mapViewport.current?.fitLayer(activeLayer.source_name)} /> : null}
        {!panel && !activeLayer && mapAreaTarget && project.plan && tool !== 'pattern_fill' && !placementAreaDrawing ? <div className="project-inspector"><header><span><strong>{mapAreaTarget.label}</strong><small>{mapAreaTarget.plantingZoneId ? 'Участок проекта' : 'Объект исходного DXF'}</small></span></header><section className="plan-summary"><strong>{mapAreaTarget.detail}</strong><span>{mapAreaTarget.plantingZoneId ? 'Участок готов к размещению' : 'Контур доступен для проверки'}</span>{mapAreaTarget.plantingZoneId ? <Button variant="primary" onClick={() => activateTool('pattern_fill')}>Разместить здесь</Button> : null}</section></div> : null}
        {!panel && !activeLayer && sourcePreview ? <div className="project-inspector"><header><span><strong>Исходный DXF</strong><small>Только просмотр</small></span></header><div className="project-inspector__empty"><Layers3 size={20} /><strong>Проверьте слои и геометрию</strong><span>Подтвердите слои, затем выберите или обведите рабочую область на карте.</span>{sourceWarnings.map((warning) => <InlineMessage key={warning} tone="warning">{warning}</InlineMessage>)}<Button variant="secondary" onClick={openLeftPanel}>Открыть слои</Button><Button variant="primary" onClick={() => navigate(`/projects/${projectId}/setup`)}>К сопоставлению</Button></div></div> : null}
        {!panel && !activeLayer && changePreview && recommendationPreview ? <RecommendationReviewPanel proposal={recommendationPreview} applying={applyChanges.isPending} onApply={() => applyChanges.mutate(recommendationPreview.change_set ?? changePreview)} onCancel={() => { setRecommendationPreview(undefined); editor.setPreview(undefined); }} /> : null}
        {!panel && !activeLayer && changePreview && !recommendationPreview && !patternPreview && !brushPreview ? <ChangeSetReviewPanel preview={changePreview} applying={applyChanges.isPending} onApply={() => applyChanges.mutate(changePreview)} onCancel={() => editor.setPreview(undefined)} /> : null}
        {!panel && !activeLayer && !changePreview && recommendationOpen && project.plan ? <RecommendationPanel zones={project.planting_zones ?? []} loading={previewRecommendation.isPending} error={previewRecommendation.error ? message(previewRecommendation.error) : undefined} onPreview={(draft) => previewRecommendation.mutate({ ...draft, base_plan_version: project.plan!.version })} onCancel={() => { recommendationAbortRef.current?.abort(); setRecommendationOpen(false); setRecommendationPreview(undefined); }} /> : null}
        {!panel && !activeLayer && !changePreview && speciesAssignmentOpen && selectedObjects.length ? <SpeciesAssignmentPanel objects={selectedObjects} shortlist={speciesShortlistQuery.data} loading={speciesShortlistQuery.isLoading} previewing={previewChanges.isPending} error={speciesShortlistQuery.error ? message(speciesShortlistQuery.error) : undefined} onAssign={previewSpeciesAssignment} onCancel={() => setSpeciesAssignmentOpen(false)} /> : null}
        {!panel && !activeLayer && (!changePreview || Boolean(patternPreview)) && (tool === 'pattern_row' || tool === 'pattern_fill' || (tool === 'draw_area' && placementAreaDrawing)) && project.plan ? <PatternToolPanel mode={tool === 'pattern_row' ? 'row' : 'fill'} zones={project.planting_zones ?? []} species={speciesQuery.data ?? []} shortlist={zoneSpeciesShortlistQuery.data} shortlistLoading={zoneSpeciesShortlistQuery.isLoading} axis={rowAxis} axisSource={rowAxisSource} selectedZoneIds={selectedPatternZoneIds} drawingZone={placementAreaDrawing} preview={patternPreview} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} loading={previewPattern.isPending || applyChanges.isPending || savePlacementZone.isPending} error={previewPattern.error ? message(previewPattern.error) : zoneSpeciesShortlistQuery.error ? message(zoneSpeciesShortlistQuery.error) : savePlacementZone.error ? message(savePlacementZone.error) : undefined} onSelectedZoneIdsChange={selectPatternZones} onDrawZone={() => { setPlacementAreaDrawing(true); setMapAreaTarget(undefined); editor.setTool('draw_area'); }} onPreview={(draft) => previewPattern.mutate({ ...draft, base_plan_version: project.plan!.version } as PatternPreviewRequest)} onApply={() => patternPreview?.change_set && applyChanges.mutate(patternPreview.change_set)} onResetPreview={() => { patternAbortRef.current?.abort(); setPatternPreview(undefined); editor.setPreview(undefined); }} onCancel={() => { patternAbortRef.current?.abort(); setPatternPreview(undefined); editor.setPreview(undefined); setRowAxis(undefined); setRowAxisSource(undefined); setPlacementAreaDrawing(false); editor.setTool('select'); }} /> : null}
        {!panel && !activeLayer && tool === 'brush' && project.plan ? <BrushToolPanel strokes={brushStrokes} zones={project.planting_zones ?? []} zoneIds={selectedPatternZoneIds} width={brushWidth} operation={brushOperation} preview={brushPreview} loading={previewBrush.isPending || applyChanges.isPending} error={previewBrush.error ? message(previewBrush.error) : undefined} onZoneIdsChange={(ids) => { selectPatternZones(ids); setBrushPreview(undefined); editor.setPreview(undefined); }} onWidth={setBrushWidth} onOperation={setBrushOperation} onPreview={(draft) => previewBrush.mutate({ ...draft, base_plan_version: project.plan!.version })} onApply={() => brushPreview?.change_set && applyChanges.mutate(brushPreview.change_set)} onClear={() => { setBrushStrokes([]); setBrushPreview(undefined); editor.setPreview(undefined); }} onCancel={() => { brushAbortRef.current?.abort(); setBrushStrokes([]); setBrushPreview(undefined); editor.setPreview(undefined); editor.setTool('select'); }} /> : null}
        {!panel && !activeLayer && !changePreview && !speciesAssignmentOpen && selectedObject ? <ObjectInspector object={selectedObject} speciesName={selectedObject.species_revision_id ? speciesNames.get(selectedObject.species_revision_id) : undefined} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} editable={!planLocked && !editorBusy && !selectedObject.locked} onSpecies={() => setSpeciesAssignmentOpen(true)} onDelete={() => setDeleteSelectionOpen(true)} /> : null}
        {!panel && !activeLayer && !changePreview && !speciesAssignmentOpen && selectedIds.length > 1 ? <GroupInspector objects={selectedObjects} disabled={editorBusy || planLocked} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} onSpecies={() => setSpeciesAssignmentOpen(true)} onCopy={() => editor.setTool('copy')} onLock={previewSelectionLock} onDelete={() => setDeleteSelectionOpen(true)} /> : null}
        </div>
      </aside>
      <Dialog open={deleteSelectionOpen} title={selectedIds.length > 1 ? `Удалить ${plantingCount(selectedIds.length)}` : 'Удалить посадку'} onClose={() => setDeleteSelectionOpen(false)} footer={<><Button variant="secondary" disabled={editorBusy} onClick={() => setDeleteSelectionOpen(false)}>Отмена</Button><Button variant="danger" icon={Trash2} loading={deleteObjects.isPending} disabled={createRelease.isPending} onClick={() => deleteObjects.mutate(selectedIds, { onSuccess: () => setDeleteSelectionOpen(false) })}>Удалить</Button></>}><p>Посадки исчезнут из текущей схемы</p></Dialog>
      <Dialog open={Boolean(zonePendingDelete)} title="Удалить рабочий участок" onClose={() => setZonePendingDelete(undefined)} footer={<><Button variant="secondary" disabled={saveManagedZones.isPending} onClick={() => setZonePendingDelete(undefined)}>Отмена</Button><Button variant="danger" icon={Trash2} loading={saveManagedZones.isPending} onClick={() => { if (!zonePendingDelete) return; saveManagedZones.mutate({ zones: draftZones.filter((zone) => zone.id !== zonePendingDelete.id) }); }}>Удалить</Button></>}><p>Участок можно удалить, если в нём ещё нет сохранённых посадок</p></Dialog>
    </main>
  </div>;
}
