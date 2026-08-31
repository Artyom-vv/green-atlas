import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiClientError, type BrushPreview, type BrushPreviewRequest, type BrushStroke, type Layer, type PatternPreview, type PatternPreviewRequest, type PlacementCheck, type PlanChangeSetDraft, type PlanObject, type PlantingZoneAssignment, type RecommendationPreview, type RecommendationRequest, type ReleasePackage } from '@green/api-client';
import { AlertTriangle, ChevronLeft, ChevronRight, Crosshair, Layers3, Maximize2, Minus, PanelRightClose, Plus, Scan, Trash2, X } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Dialog, IconButton, InlineMessage, Progress } from '@green/ui';
import { AppHeader } from '../domain-ui/AppHeader';
import { ReleasePanel } from '../domain-ui/ReleasePanel';
import { ChangeSetReviewPanel } from '../domain-ui/ChangeSetReviewPanel';
import { BrushToolPanel } from '../domain-ui/BrushToolPanel';
import { GroupInspector } from '../domain-ui/GroupInspector';
import { GrowthHorizonControl, type GrowthHorizon } from '../domain-ui/GrowthHorizonControl';
import { LayerInspector } from '../domain-ui/LayerInspector';
import { MapToolbar, type MapTool } from '../domain-ui/MapToolbar';
import { MapViewport, type MapAreaTarget, type MapExtent, type MapViewportHandle, type MapHoverTarget } from '../domain-ui/MapViewport';
import { paddedMapExtent } from '../domain-ui/mapExtent';
import { ObjectInspector } from '../domain-ui/ObjectInspector';
import { PatternToolPanel } from '../domain-ui/PatternToolPanel';
import { PlantingZonesPanel } from '../domain-ui/PlantingZonesPanel';
import { assignmentFromGeometry } from '../domain-ui/plantingZones';
import { ProjectConflictNotice } from '../domain-ui/ProjectConflictNotice';
import { isProjectConflict } from '../domain-ui/projectConflict';
import { ProjectLayers } from '../domain-ui/ProjectLayers';
import { RecommendationPanel } from '../domain-ui/RecommendationPanel';
import { RecommendationReviewPanel } from '../domain-ui/RecommendationReviewPanel';
import { SpeciesAssignmentPanel } from '../domain-ui/SpeciesAssignmentPanel';
import { ValidationPanel } from '../domain-ui/ValidationPanel';
import { useWorkspaceEditor } from '../features/workspace/useWorkspaceEditor';

type WorkspacePanel = 'zones' | 'issues' | 'export' | null;
type MapGeometryMetadata = { returned_features?: number; total_matches?: number; truncated?: boolean };
type BufferedMapRequest = { projectId: string; extent: MapExtent; resolution: number };

const EMPTY_PLAN_OBJECTS: PlanObject[] = [];
const EMPTY_LAYERS: Layer[] = [];
const SceneReview = lazy(async () => ({ default: (await import('../domain-ui/SceneReview')).SceneReview }));
const message = (error: unknown) => error instanceof ApiClientError ? error.message : error instanceof Error ? error.message : 'Неизвестная ошибка';
const plantingCount = (count: number) => {
  const modulo100 = count % 100;
  const modulo10 = count % 10;
  if (modulo100 >= 11 && modulo100 <= 14) return `${count} посадок`;
  if (modulo10 === 1) return `${count} посадка`;
  if (modulo10 >= 2 && modulo10 <= 4) return `${count} посадки`;
  return `${count} посадок`;
};

function bufferedMapRequest(extent: MapExtent, resolution: number) {
  const width = Math.max(1, extent[2] - extent[0]);
  const height = Math.max(1, extent[3] - extent[1]);
  return {
    extent: [extent[0] - width * 0.8, extent[1] - height * 0.8, extent[2] + width * 0.8, extent[3] + height * 0.8].map((value) => Number(value.toFixed(1))) as MapExtent,
    resolution: Number(resolution.toFixed(3)),
  };
}

function RailPanelHeader({ title, subtitle, onBack, onClose }: { title: string; subtitle: string; onBack: () => void; onClose: () => void }) {
  return <header className="rail-panel__header"><IconButton icon={ChevronLeft} label="Назад к сводке" variant="ghost" onClick={onBack} /><span><strong>{title}</strong><small>{subtitle}</small></span><IconButton icon={X} label="Свернуть боковую панель" variant="ghost" onClick={onClose} /></header>;
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
  const [mapRequest, setMapRequest] = useState<BufferedMapRequest>();
  const [draftZones, setDraftZones] = useState<PlantingZoneAssignment[]>([]);
  const [release, setRelease] = useState<ReleasePackage>();
  const [rowAxis, setRowAxis] = useState<{ type: 'LineString'; coordinates: number[][] }>();
  const [patternPreview, setPatternPreview] = useState<PatternPreview>();
  const [selectedPatternZoneIds, setSelectedPatternZoneIds] = useState<string[]>([]);
  const [placementAreaDrawing, setPlacementAreaDrawing] = useState(false);
  const [recommendationOpen, setRecommendationOpen] = useState(false);
  const [recommendationPreview, setRecommendationPreview] = useState<RecommendationPreview>();
  const [brushStrokes, setBrushStrokes] = useState<BrushStroke[]>([]);
  const [brushPreview, setBrushPreview] = useState<BrushPreview>();
  const [speciesAssignmentOpen, setSpeciesAssignmentOpen] = useState(false);
  const [growthHorizon, setGrowthHorizon] = useState<GrowthHorizon>();
  const [sceneOpen, setSceneOpen] = useState(false);
  const [sceneHorizon, setSceneHorizon] = useState<0 | 5 | 10 | 20>(0);

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
    if (mapRequestTimerRef.current !== undefined) window.clearTimeout(mapRequestTimerRef.current);
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    mapRequestTimerRef.current = undefined;
    placementTimerRef.current = undefined;
    setPanel(null);
    setLeftOpen(false);
    setRightOpen(true);
    setActiveLayerId(undefined);
    setVisibility({});
    setDeleteSelectionOpen(false);
    setCursor(undefined);
    setMapHoverTarget(undefined);
    setPlacementCheck(undefined);
    setMapRequest(undefined);
    setDraftZones([]);
    setRelease(undefined);
    setRowAxis(undefined);
    setPatternPreview(undefined);
    setSelectedPatternZoneIds([]);
    setPlacementAreaDrawing(false);
    setRecommendationOpen(false);
    setRecommendationPreview(undefined);
    setBrushStrokes([]);
    setBrushPreview(undefined);
    setSpeciesAssignmentOpen(false);
    setGrowthHorizon(undefined);
    setSceneOpen(false);
    setSceneHorizon(0);
  }, [projectId]);

  useEffect(() => {
    if (!project || initializedProjectRef.current === project.id) return;
    initializedProjectRef.current = project.id;
    setDraftZones(project.planting_zones ?? []);
    setPanel(project.plan ? null : 'zones');
  }, [project]);

  useEffect(() => () => {
    if (mapRequestTimerRef.current !== undefined) window.clearTimeout(mapRequestTimerRef.current);
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    placementAbortRef.current?.abort();
    patternAbortRef.current?.abort();
    recommendationAbortRef.current?.abort();
    brushAbortRef.current?.abort();
  }, []);

  const initialExtent = useMemo(() => paddedMapExtent(project?.source_file?.bounds, 20, sourcePreview ? 0.3 : 0), [project?.source_file?.bounds, sourcePreview]);
  const activeLayer = useMemo(() => layers.find((layer) => layer.id === activeLayerId), [activeLayerId, layers]);
  const hiddenLayerNames = useMemo(() => layers.filter((layer) => visibility[layer.id] === false).map((layer) => layer.source_name), [layers, visibility]);
  const selectedId = selectedIds.length === 1 ? selectedIds[0] : undefined;
  const selectedObject = useMemo(() => planObjects.find((item) => item.id === selectedId), [planObjects, selectedId]);
  const selectedPatternZone = useMemo(() => (project?.planting_zones ?? []).find((zone) => zone.id === selectedPatternZoneIds[0]), [project?.planting_zones, selectedPatternZoneIds]);
  const selectedObjects = useMemo(() => {
    const ids = new Set(selectedIds);
    return planObjects.filter((item) => item.id && ids.has(item.id));
  }, [planObjects, selectedIds]);
  const selectedIdsKey = [...selectedIds].sort().join(':');
  const speciesShortlistQuery = useQuery({ queryKey: ['species-shortlist', projectId, selectedIdsKey], queryFn: () => api.shortlistSpecies(projectId, selectedIds), enabled: Boolean(speciesAssignmentOpen && selectedIds.length), staleTime: 30_000 });
  const speciesNames = useMemo(() => new Map((speciesQuery.data ?? []).map((item) => [item.id, item.common_name])), [speciesQuery.data]);
  const hasGrowthForecasts = planObjects.some((object) => object.canopy_forecast?.length);
  const issues = project?.plan?.issues ?? [];
  const issueCount = issues.length;
  const placementPreview = useMemo(() => {
    if (!cursor || (tool !== 'add_tree' && tool !== 'add_shrub')) return undefined;
    const kind = tool === 'add_tree' ? 'tree' : 'shrub';
    const check = placementCheck && Math.abs(placementCheck.x - cursor[0]) < 0.01 && Math.abs(placementCheck.y - cursor[1]) < 0.01 ? placementCheck : undefined;
    return { coordinate: cursor, radius: kind === 'tree' ? 1.6 : 0.65, status: check?.status ?? 'unknown' } as const;
  }, [cursor, placementCheck, tool]);

  const openLeftPanel = useCallback(() => { setRightOpen(false); setLeftOpen(true); }, []);
  const openRightPanel = useCallback(() => { setLeftOpen(false); setRightOpen(true); }, []);
  const closeRightPanel = useCallback(() => setRightOpen(false), []);
  const returnToInspector = useCallback(() => setPanel(projectHasPlan ? null : 'zones'), [projectHasPlan]);

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
    mapRequestTimerRef.current = window.setTimeout(() => { setMapRequest(next); mapRequestTimerRef.current = undefined; }, 100);
  }, [projectId]);

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
  const applyChanges = useMutation({ mutationFn: () => {
    if (!changePreview) throw new Error('Предпросмотр изменений недоступен');
    return api.applyPlanChanges(projectId, changePreview);
  }, onSuccess: async (result) => { setPatternPreview(undefined); setRecommendationOpen(false); setRecommendationPreview(undefined); setBrushStrokes([]); setBrushPreview(undefined); setRowAxis(undefined); setSpeciesAssignmentOpen(false); editor.setPreview(undefined); editor.setTool('select'); editor.select([...(result.added_ids ?? []), ...(result.updated_ids ?? [])], 'replace'); await refresh(); } });
  const deleteObjects = useMutation({ mutationFn: (ids: string[]) => api.deletePlanObjects(projectId, ids), onSuccess: async () => { editor.clearSelection(); await refresh(); } });
  const undoChange = useMutation({ mutationFn: () => api.undoPlanChange(projectId), onSuccess: async () => { editor.clearSelection(); editor.setTool('select'); await refresh(); } });
  const redoChange = useMutation({ mutationFn: () => api.redoPlanChange(projectId), onSuccess: async () => { editor.clearSelection(); editor.setTool('select'); await refresh(); } });
  const createRelease = useMutation({ mutationFn: (mode: 'draft' | 'final') => api.createRelease(projectId, { mode, scene_horizon: 20 }), onSuccess: setRelease });
  const busy = addObject.isPending || savePlacementZone.isPending || previewChanges.isPending || previewPattern.isPending || previewRecommendation.isPending || previewBrush.isPending || applyChanges.isPending || deleteObjects.isPending || undoChange.isPending || redoChange.isPending;
  // Export captures one durable version of the plan. Do not let a normal map
  // click race that snapshot and surface an avoidable version conflict.
  const editorBusy = busy || createRelease.isPending;

  const reloadAfterConflict = useCallback(async () => {
    // React Query keeps a mutation error until it is reset. Reloading only
    // the project data would therefore leave the old conflict notice mounted
    // and the editor in the stale add/move mode.
    createManualPlan.reset();
    savePlacementZone.reset();
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
    setPatternPreview(undefined);
    setPlacementAreaDrawing(false);
    setRecommendationOpen(false);
    setRecommendationPreview(undefined);
    setBrushStrokes([]);
    setBrushPreview(undefined);
    setSpeciesAssignmentOpen(false);
    editor.reset();
    await refresh();
  }, [addObject, applyChanges, createManualPlan, createRelease, deleteObjects, editor, previewBrush, previewChanges, previewPattern, previewRecommendation, redoChange, refresh, savePlacementZone, undoChange]);

  const addMapArea = useCallback((geometry: PlantingZoneAssignment['geometry'], label: string, sourceId?: string) => {
    setDraftZones((current) => {
      if (sourceId && current.some((zone) => zone.id === sourceId)) return current;
      return [...current, assignmentFromGeometry(geometry, current.length + 1, label, sourceId)];
    });
    setPanel('zones');
    openRightPanel();
  }, [openRightPanel]);

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
    if (nextTool !== 'pattern_row') setRowAxis(undefined);
    if (nextTool === 'pattern_row' || nextTool === 'pattern_fill' || nextTool === 'brush') {
      editor.clearSelection();
      setActiveLayerId(undefined);
      setPanel(null);
      openRightPanel();
    }
    if (nextTool === 'pattern_fill' && mapAreaTarget?.plantingZoneId) setSelectedPatternZoneIds([mapAreaTarget.plantingZoneId]);
    editor.setTool(tool === nextTool && nextTool !== 'select' ? 'select' : nextTool);
  }, [editor, editorBusy, mapAreaTarget?.plantingZoneId, openRightPanel, planLocked, projectHasPlan, sourcePreview, tool]);

  const previewSelectionTransform = (mode: 'move' | 'copy', coordinate: [number, number]) => {
    const plan = project?.plan;
    if (!plan || !selectedObjects.length) return;
    const center = selectedObjects.reduce(([x, y], object) => [x + object.x, y + object.y] as [number, number], [0, 0] as [number, number]);
    const delta: [number, number] = [coordinate[0] - center[0] / selectedObjects.length, coordinate[1] - center[1] / selectedObjects.length];
    const operations: PlanChangeSetDraft['operations'] = mode === 'move'
      ? selectedObjects.flatMap((object) => object.id ? [{ type: 'update' as const, object_id: object.id, changes: { x: object.x + delta[0], y: object.y + delta[1] } }] : [])
      : selectedObjects.map((object) => ({
        type: 'add' as const,
        object: {
          kind: object.kind,
          x: object.x + delta[0],
          y: object.y + delta[1],
          radius: object.layout_radius_m ?? object.radius,
          layout_radius_m: object.layout_radius_m ?? object.radius,
          size_class: object.size_class,
          species_revision_id: object.species_revision_id,
          pattern_id: object.pattern_id,
          group_ids: object.group_ids ?? [],
          locked: false,
        },
      }));
    previewChanges.mutate({
      base_plan_version: plan.version,
      source: 'group',
      label: mode === 'move' ? `Перемещение группы (${selectedObjects.length})` : `Копирование группы (${selectedObjects.length})`,
      policy: 'all_or_nothing',
      operations,
    });
  };

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
    if ((tool === 'move' || tool === 'copy') && selectedObjects.length && !mapEditInFlightRef.current) {
      mapEditInFlightRef.current = true;
      previewSelectionTransform(tool, coordinate);
      mapEditInFlightRef.current = false;
    }
  };

  useEffect(() => {
    const handleHistoryShortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select, [contenteditable="true"]')) return;
      if (event.key === 'Escape') {
        if (sceneOpen) {
          setSceneOpen(false);
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
        setSpeciesAssignmentOpen(false);
        editor.setPreview(undefined);
        editor.setTool('select');
        return;
      }
      if (event.key === 'Enter' && changePreview?.can_apply && !editorBusy) {
        event.preventDefault();
        applyChanges.mutate();
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
  }, [applyChanges, changePreview?.can_apply, editor, editorBusy, historyQuery.data?.can_redo, historyQuery.data?.can_undo, planLocked, redoChange, sceneOpen, selectedIds.length, undoChange]);

  if (projectQuery.isLoading) return <div className="app-shell"><AppHeader /><main className="center-status"><Progress label="Загрузка рабочей области" /></main></div>;
  if (!project) return <div className="app-shell"><AppHeader /><main className="center-status"><InlineMessage tone="error">{message(projectQuery.error)}</InlineMessage></main></div>;

  const operationError = createManualPlan.error ?? savePlacementZone.error ?? addObject.error ?? previewChanges.error ?? previewPattern.error ?? previewRecommendation.error ?? previewBrush.error ?? applyChanges.error ?? deleteObjects.error ?? undoChange.error ?? redoChange.error ?? mapGeometryQuery.error;
  const showMapStatus = Boolean(
    (placementCheck && (tool === 'add_tree' || tool === 'add_shrub'))
    || mapGeometryQuery.isFetching
    || mapGeometryMetadata?.truncated,
  );

  return <div className="app-shell workspace-screen">
    <AppHeader workspace projectName={project.name} onReview={project.plan ? () => { setPanel('issues'); openRightPanel(); } : undefined} onExport={project.plan ? () => { setPanel('export'); openRightPanel(); } : undefined} exporting={createRelease.isPending} onUndo={!planLocked && historyQuery.data?.can_undo ? () => undoChange.mutate() : undefined} onRedo={!planLocked && historyQuery.data?.can_redo ? () => redoChange.mutate() : undefined} undoLabel={historyQuery.data?.undo_label} redoLabel={historyQuery.data?.redo_label} historyBusy={undoChange.isPending || redoChange.isPending} actionsDisabled={editorBusy || createManualPlan.isPending} />
    <main className={`workspace-layout ${leftOpen ? '' : 'is-left-collapsed'} ${rightOpen ? '' : 'is-right-collapsed'}`}>
      <aside className="workspace-left"><ProjectLayers layers={layers} visibility={visibility} activeLayerId={activeLayerId} onVisibility={(id, visible) => setVisibility((current) => ({ ...current, [id]: visible }))} onSelect={(id) => { setActiveLayerId((current) => current === id ? undefined : id); editor.clearSelection(); setPanel(null); openRightPanel(); }} onClose={() => setLeftOpen(false)} /></aside>
      <aside className="workspace-left-collapsed"><IconButton icon={ChevronRight} label="Развернуть слои" variant="ghost" onClick={openLeftPanel} /><IconButton icon={Layers3} label="Слои" active onClick={openLeftPanel} /></aside>
      <section className={`map-canvas ${rightOpen ? '' : 'has-right-dock'}`}>
        <MapViewport key={projectId} ref={mapViewport} geometry={mapGeometryQuery.data?.feature_collection as Record<string, unknown> | undefined} geometryRevision={project.geometry_version} initialExtent={initialExtent} objects={planObjects} growthHorizon={growthHorizon} draftPlantingZones={!projectHasPlan ? draftZones : undefined} hiddenLayerNames={hiddenLayerNames} selectedIds={selectedIds} highlightedPlantingZoneId={selectedPatternZoneIds.length === 1 ? selectedPatternZoneIds[0] : undefined} focusGeometry={selectedPatternZone?.geometry} placementPreview={placementPreview} changePreview={changePreview} tool={tool} onDrawArea={(geometry) => {
          if (projectHasPlan && placementAreaDrawing && !planLocked) {
            const zone = assignmentFromGeometry(geometry, (project.planting_zones?.length ?? 0) + 1, `Участок ${project.planting_zones?.length ?? 0}`);
            savePlacementZone.mutate(zone);
            return;
          }
          if (projectHasPlan || planLocked) return;
          addMapArea(geometry, `Ручной участок ${draftZones.length + 1}`);
          editor.setTool('select');
        }} onDrawAxis={(geometry) => { setRowAxis(geometry); setPatternPreview(undefined); editor.setPreview(undefined); openRightPanel(); }} onDrawBrush={(stroke, mode) => { setBrushStrokes((current) => mode === 'replace' ? [stroke] : [...current, stroke]); setBrushPreview(undefined); editor.setPreview(undefined); openRightPanel(); }} onMapArea={(target, mode) => {
          if (!projectHasPlan) {
            if (target.selectable && target.geometry) addMapArea(target.geometry, target.label, target.sourceId);
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
          setPanel(null);
          editor.clearSelection();
          openRightPanel();
        }} onMapHover={setMapHoverTarget} onSelect={(id, mode = 'replace') => {
          if (!projectHasPlan) return;
          if (!id) { if (mode === 'replace') editor.clearSelection(); return; }
          editor.select([id], mode);
          setMapAreaTarget(undefined);
          setActiveLayerId(undefined);
          setPanel(null);
          openRightPanel();
        }} onSelectMany={(ids, mode) => {
          if (!projectHasPlan) return;
          editor.select(ids, mode);
          setActiveLayerId(undefined);
          setPanel(null);
          openRightPanel();
        }} onCoordinate={handleCoordinate} onPointerCoordinate={handlePointerCoordinate} onExtentChange={handleMapExtent} />
        {!sceneOpen && mapHoverTarget && (tool === 'select' || tool === 'pattern_fill') ? <div className={`map-hover-hint ${mapHoverTarget.pixel[0] > 520 ? 'is-left' : ''}`} style={{ left: mapHoverTarget.pixel[0] + 14, top: mapHoverTarget.pixel[1] + 14 }} role="status">{mapHoverTarget.items.map((item) => <div className="map-hover-hint__item" key={item.id}><strong>{item.label}</strong><span>{item.detail}</span></div>)}</div> : null}
        {!sceneOpen && projectHasPlan ? <div className="map-edit-tools"><MapToolbar tool={tool} onTool={activateTool} editable={!sourcePreview && !planLocked && !editorBusy} canDelete={selectedIds.length > 0} onDelete={() => setDeleteSelectionOpen(true)} /></div> : null}
        {!sceneOpen ? <div className="map-zoom-tools"><IconButton icon={Plus} label="Увеличить" variant="ghost" onClick={() => mapViewport.current?.zoomIn()} /><IconButton icon={Minus} label="Уменьшить" variant="ghost" onClick={() => mapViewport.current?.zoomOut()} /><IconButton icon={Maximize2} label="Показать весь чертёж" variant="ghost" onClick={() => mapViewport.current?.fit()} /></div> : null}
        {!sceneOpen && project.plan ? <div className="map-plan-focus"><IconButton icon={Crosshair} label="Показать посадки" variant="ghost" onClick={() => mapViewport.current?.fitPlan()} /></div> : null}
        {!rightOpen ? <div className="right-dock"><IconButton icon={ChevronLeft} label="Развернуть панель" variant="ghost" onClick={openRightPanel} /><IconButton icon={project.plan ? AlertTriangle : Scan} label={project.plan ? 'Проверка' : 'Участки'} variant="ghost" onClick={() => { setPanel(project.plan ? 'issues' : 'zones'); openRightPanel(); }} /></div> : null}
        {showMapStatus ? <div className="map-statusbar">{placementCheck && (tool === 'add_tree' || tool === 'add_shrub') ? <span className={`placement-check placement-check--${placementCheck.status}`} role="status">{placementCheck.reason}</span> : null}{mapGeometryQuery.isFetching ? <span className="map-stream-status">Обновляем карту</span> : null}{mapGeometryMetadata?.truncated ? <span className="map-lod-warning" role="status">Приблизьте карту, чтобы увидеть детали</span> : null}</div> : null}
        {busy ? <div className="map-busy"><Progress label="Сохраняем изменения" /></div> : null}
        {operationError ? <div className="map-operation-error">{isProjectConflict(operationError) ? <ProjectConflictNotice error={operationError} onReload={() => void reloadAfterConflict()} reloading={projectQuery.isFetching} /> : <InlineMessage tone="error">{message(operationError)}</InlineMessage>}</div> : null}
        {sceneOpen ? <Suspense fallback={<div className="scene-review scene-review--loading"><Progress label="Загрузка 3D" /></div>}><SceneReview snapshot={sceneQuery.data} horizon={sceneHorizon} selectedIds={selectedIds} loading={sceneQuery.isLoading || sceneQuery.isFetching} error={sceneQuery.error ? message(sceneQuery.error) : undefined} onHorizon={setSceneHorizon} onSelect={(id) => editor.select([id], 'replace')} onClose={() => setSceneOpen(false)} /></Suspense> : null}
      </section>
      <aside className="workspace-right">
        {!panel ? <button className="right-close" type="button" aria-label="Свернуть инспектор" onClick={closeRightPanel}><PanelRightClose size={16} /></button> : null}
        {panel === 'zones' && !project.plan ? <div className="workspace-zones-panel"><button className="right-close" type="button" aria-label="Свернуть панель участков" onClick={closeRightPanel}><PanelRightClose size={16} /></button><PlantingZonesPanel assignments={draftZones} drawingManual={tool === 'draw_area'} saving={createManualPlan.isPending} error={createManualPlan.error ? message(createManualPlan.error) : undefined} onRemove={(id) => setDraftZones((current) => current.filter((item) => item.id !== id))} onSave={() => createManualPlan.mutate()} onManual={() => editor.setTool('draw_area')} onCancelManual={() => editor.setTool('select')} /></div> : null}
        {panel === 'issues' ? <div className="rail-panel"><RailPanelHeader title="Проверка плана" subtitle={issueCount ? `${issueCount} замечания` : 'Нарушений нет'} onBack={returnToInspector} onClose={closeRightPanel} /><ValidationPanel issues={issues} onLocate={(id) => { if (id) editor.select([id], 'replace'); else editor.clearSelection(); editor.setTool('select'); setPanel(null); if (id) requestAnimationFrame(() => mapViewport.current?.fitSelection(id)); }} /></div> : null}
        {panel === 'export' && project.plan ? <div className="rail-panel export-panel"><RailPanelHeader title="Выпускной пакет" subtitle="Ревизия для дальнейшей работы" onBack={returnToInspector} onClose={closeRightPanel} /><div className="rail-panel__content"><ReleasePanel plan={project.plan} release={release} loading={createRelease.isPending} error={createRelease.error ? message(createRelease.error) : undefined} onCreate={(mode) => createRelease.mutate(mode)} onDownload={(path) => { window.location.href = api.downloadUrl(path); }} /></div></div> : null}
        {!panel && activeLayer ? <LayerInspector layer={activeLayer} visible={visibility[activeLayer.id] !== false} onVisibility={(visible) => setVisibility((current) => ({ ...current, [activeLayer.id]: visible }))} onFit={() => mapViewport.current?.fitLayer(activeLayer.source_name)} /> : null}
        {!panel && !activeLayer && mapAreaTarget && project.plan && tool !== 'pattern_fill' && !placementAreaDrawing ? <div className="project-inspector"><header><span><strong>{mapAreaTarget.label}</strong><small>{mapAreaTarget.plantingZoneId ? 'Участок проекта' : 'Объект исходного DXF'}</small></span></header><section className="plan-summary"><strong>{mapAreaTarget.detail}</strong><span>{mapAreaTarget.plantingZoneId ? 'Участок готов к размещению' : 'Контур доступен для проверки'}</span>{mapAreaTarget.plantingZoneId ? <Button variant="primary" onClick={() => activateTool('pattern_fill')}>Разместить здесь</Button> : null}</section></div> : null}
        {!panel && !activeLayer && sourcePreview ? <div className="project-inspector"><header><span><strong>Исходный DXF</strong><small>Только просмотр</small></span></header><div className="project-inspector__empty"><Layers3 size={20} /><strong>Проверьте слои и геометрию</strong><span>Подтвердите слои, затем выберите или обведите рабочую область на карте.</span>{sourceWarnings.map((warning) => <InlineMessage key={warning} tone="warning">{warning}</InlineMessage>)}<Button variant="secondary" onClick={openLeftPanel}>Открыть слои</Button><Button variant="primary" onClick={() => navigate(`/projects/${projectId}/setup`)}>К сопоставлению</Button></div></div> : null}
        {!panel && !activeLayer && changePreview && recommendationPreview ? <RecommendationReviewPanel proposal={recommendationPreview} applying={applyChanges.isPending} onApply={() => applyChanges.mutate()} onCancel={() => { setRecommendationPreview(undefined); editor.setPreview(undefined); }} /> : null}
        {!panel && !activeLayer && changePreview && !recommendationPreview ? <ChangeSetReviewPanel preview={changePreview} applying={applyChanges.isPending} note={patternPreview ? patternPreview.accepted_count === patternPreview.requested_count ? `Размещено ${patternPreview.accepted_count}` : `Размещено ${patternPreview.accepted_count} из ${patternPreview.requested_count}, причина: ${patternPreview.skipped[0]?.reason ?? 'недостаточно допустимых мест'}` : brushPreview ? `Будет добавлено ${brushPreview.added_count}${brushPreview.skipped.length ? `, не размещено ${brushPreview.skipped.length}` : ''}` : undefined} rejectedReasons={patternPreview?.skipped.map((item) => item.reason)} onApply={() => applyChanges.mutate()} onCancel={() => { setPatternPreview(undefined); setBrushPreview(undefined); editor.setPreview(undefined); }} /> : null}
        {!panel && !activeLayer && !changePreview && recommendationOpen && project.plan ? <RecommendationPanel zones={project.planting_zones ?? []} loading={previewRecommendation.isPending} error={previewRecommendation.error ? message(previewRecommendation.error) : undefined} onPreview={(draft) => previewRecommendation.mutate({ ...draft, base_plan_version: project.plan!.version })} onCancel={() => { recommendationAbortRef.current?.abort(); setRecommendationOpen(false); setRecommendationPreview(undefined); }} /> : null}
        {!panel && !activeLayer && !changePreview && speciesAssignmentOpen && selectedObjects.length ? <SpeciesAssignmentPanel objects={selectedObjects} shortlist={speciesShortlistQuery.data} loading={speciesShortlistQuery.isLoading} previewing={previewChanges.isPending} error={speciesShortlistQuery.error ? message(speciesShortlistQuery.error) : undefined} onAssign={previewSpeciesAssignment} onCancel={() => setSpeciesAssignmentOpen(false)} /> : null}
        {!panel && !activeLayer && !changePreview && (tool === 'pattern_row' || tool === 'pattern_fill' || (tool === 'draw_area' && placementAreaDrawing)) && project.plan ? <PatternToolPanel mode={tool === 'pattern_row' ? 'row' : 'fill'} zones={project.planting_zones ?? []} species={speciesQuery.data ?? []} axis={rowAxis} selectedZoneIds={selectedPatternZoneIds} drawingZone={placementAreaDrawing} loading={previewPattern.isPending || savePlacementZone.isPending} error={previewPattern.error ? message(previewPattern.error) : savePlacementZone.error ? message(savePlacementZone.error) : undefined} resultNote={patternPreview && !patternPreview.change_set ? `Допустимых позиций нет: ${patternPreview.skipped[0]?.reason ?? 'измените параметры'}` : undefined} onSelectedZoneIdsChange={setSelectedPatternZoneIds} onDrawZone={() => { setPlacementAreaDrawing(true); setMapAreaTarget(undefined); editor.setTool('draw_area'); }} onPreview={(draft) => previewPattern.mutate({ ...draft, base_plan_version: project.plan!.version } as PatternPreviewRequest)} onCancel={() => { patternAbortRef.current?.abort(); setPatternPreview(undefined); setRowAxis(undefined); setPlacementAreaDrawing(false); editor.setTool('select'); }} /> : null}
        {!panel && !activeLayer && !changePreview && tool === 'brush' && project.plan ? <BrushToolPanel strokes={brushStrokes} loading={previewBrush.isPending} error={previewBrush.error ? message(previewBrush.error) : undefined} resultNote={brushPreview && !brushPreview.change_set ? `Изменений нет. Пропущено: ${brushPreview.skipped.length}.` : undefined} onPreview={(draft) => previewBrush.mutate({ ...draft, base_plan_version: project.plan!.version })} onClear={() => { setBrushStrokes([]); setBrushPreview(undefined); editor.setPreview(undefined); }} onCancel={() => { brushAbortRef.current?.abort(); setBrushStrokes([]); setBrushPreview(undefined); editor.setTool('select'); }} /> : null}
        {!panel && !activeLayer && !changePreview && !speciesAssignmentOpen && selectedObject ? <ObjectInspector object={selectedObject} speciesName={selectedObject.species_revision_id ? speciesNames.get(selectedObject.species_revision_id) : undefined} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} editable={!planLocked && !editorBusy && !selectedObject.locked} onSpecies={() => setSpeciesAssignmentOpen(true)} onMove={() => editor.setTool('move')} onDelete={() => setDeleteSelectionOpen(true)} /> : null}
        {!panel && !activeLayer && !changePreview && !speciesAssignmentOpen && selectedIds.length > 1 ? <GroupInspector objects={selectedObjects} disabled={editorBusy || planLocked} growthHorizon={growthHorizon} onGrowthHorizon={setGrowthHorizon} onSpecies={() => setSpeciesAssignmentOpen(true)} onMove={() => editor.setTool('move')} onCopy={() => editor.setTool('copy')} onLock={previewSelectionLock} onDelete={() => setDeleteSelectionOpen(true)} /> : null}
        {!panel && !activeLayer && !changePreview && !recommendationOpen && !speciesAssignmentOpen && !selectedIds.length && tool !== 'pattern_row' && tool !== 'pattern_fill' && tool !== 'brush' && project.plan ? <div className="project-inspector"><header><span><strong>План озеленения</strong><small>{plantingCount(planObjects.length)}</small></span></header><section className="plan-summary"><strong>{planObjects.length ? 'Выберите группу или участок' : 'Создайте первую схему'}</strong><span>{planObjects.length ? 'Можно проверить или изменить посадки' : 'Сервис рассчитает допустимые позиции'}</span><Button variant="primary" onClick={() => activateTool('pattern_fill')}>Разместить посадки</Button>{sourceWarnings.length ? <InlineMessage tone="warning">В исходном DXF есть замечания</InlineMessage> : null}</section>{hasGrowthForecasts ? <GrowthHorizonControl value={growthHorizon} onChange={setGrowthHorizon} /> : null}{planObjects.length ? <section className="project-inspector__actions"><Button variant="secondary" icon={Crosshair} onClick={() => mapViewport.current?.fitPlan()}>Показать посадки</Button></section> : null}</div> : null}
      </aside>
      <Dialog open={deleteSelectionOpen} title={selectedIds.length > 1 ? `Удалить ${plantingCount(selectedIds.length)}` : 'Удалить посадку'} onClose={() => setDeleteSelectionOpen(false)} footer={<><Button variant="secondary" disabled={editorBusy} onClick={() => setDeleteSelectionOpen(false)}>Отмена</Button><Button variant="danger" icon={Trash2} loading={deleteObjects.isPending} disabled={createRelease.isPending} onClick={() => deleteObjects.mutate(selectedIds, { onSuccess: () => setDeleteSelectionOpen(false) })}>Удалить</Button></>}><p>Посадки исчезнут из текущей схемы</p></Dialog>
    </main>
  </div>;
}
