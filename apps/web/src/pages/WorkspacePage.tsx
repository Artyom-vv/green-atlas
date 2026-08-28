import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiClientError, type ExportArtifact, type Layer, type PlacementCheck, type PlanObject, type PlantingZoneAssignment } from '@green/api-client';
import { AlertTriangle, ChevronLeft, ChevronRight, Crosshair, Layers3, Maximize2, Minus, PanelRightClose, Plus, Scan, Trash2, X } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Dialog, IconButton, InlineMessage, Progress } from '@green/ui';
import { AppHeader } from '../domain-ui/AppHeader';
import { ExportArtifactSection } from '../domain-ui/ExportArtifactSection';
import { LayerInspector } from '../domain-ui/LayerInspector';
import { MapToolbar, type MapTool } from '../domain-ui/MapToolbar';
import { MapViewport, type MapExtent, type MapViewportHandle, type MapHoverTarget } from '../domain-ui/MapViewport';
import { paddedMapExtent } from '../domain-ui/mapExtent';
import { ObjectInspector } from '../domain-ui/ObjectInspector';
import { PlantingZonesPanel } from '../domain-ui/PlantingZonesPanel';
import { assignmentFromGeometry } from '../domain-ui/plantingZones';
import { ProjectConflictNotice } from '../domain-ui/ProjectConflictNotice';
import { isProjectConflict } from '../domain-ui/projectConflict';
import { ProjectLayers } from '../domain-ui/ProjectLayers';
import { ValidationPanel } from '../domain-ui/ValidationPanel';

type WorkspacePanel = 'zones' | 'issues' | 'export' | null;
type MapGeometryMetadata = { returned_features?: number; total_matches?: number; truncated?: boolean };
type BufferedMapRequest = { projectId: string; extent: MapExtent; resolution: number };

const EMPTY_PLAN_OBJECTS: PlanObject[] = [];
const EMPTY_LAYERS: Layer[] = [];
const message = (error: unknown) => error instanceof ApiClientError ? error.message : error instanceof Error ? error.message : 'Неизвестная ошибка';

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
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [deleteSelectionOpen, setDeleteSelectionOpen] = useState(false);
  const [tool, setTool] = useState<MapTool>('select');
  const [cursor, setCursor] = useState<[number, number]>();
  const [mapHoverTarget, setMapHoverTarget] = useState<MapHoverTarget>();
  const [placementCheck, setPlacementCheck] = useState<PlacementCheck>();
  const [mapRequest, setMapRequest] = useState<BufferedMapRequest>();
  const [draftZones, setDraftZones] = useState<PlantingZoneAssignment[]>([]);
  const [exportArtifact, setExportArtifact] = useState<ExportArtifact>();

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
    if (mapRequestTimerRef.current !== undefined) window.clearTimeout(mapRequestTimerRef.current);
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    mapRequestTimerRef.current = undefined;
    placementTimerRef.current = undefined;
    setPanel(null);
    setLeftOpen(false);
    setRightOpen(true);
    setActiveLayerId(undefined);
    setVisibility({});
    setSelectedIds([]);
    setDeleteSelectionOpen(false);
    setTool('select');
    setCursor(undefined);
    setMapHoverTarget(undefined);
    setPlacementCheck(undefined);
    setMapRequest(undefined);
    setDraftZones([]);
    setExportArtifact(undefined);
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
  }, []);

  const initialExtent = useMemo(() => paddedMapExtent(project?.source_file?.bounds, 20, sourcePreview ? 0.3 : 0), [project?.source_file?.bounds, sourcePreview]);
  const activeLayer = useMemo(() => layers.find((layer) => layer.id === activeLayerId), [activeLayerId, layers]);
  const hiddenLayerNames = useMemo(() => layers.filter((layer) => visibility[layer.id] === false).map((layer) => layer.source_name), [layers, visibility]);
  const selectedId = selectedIds.length === 1 ? selectedIds[0] : undefined;
  const selectedObject = useMemo(() => planObjects.find((item) => item.id === selectedId), [planObjects, selectedId]);
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
    setCursor(coordinate);
    const adding = tool === 'add_tree' || tool === 'add_shrub';
    if (!coordinate || !adding || !projectHasPlan || sourcePreview) {
      if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
      placementAbortRef.current?.abort();
      setPlacementCheck(undefined);
      return;
    }
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
      setTool('select');
      await refresh({ mapGeometry: true });
      openRightPanel();
    },
  });
  const addObject = useMutation({ mutationFn: ({ kind, coordinate }: { kind: 'tree' | 'shrub'; coordinate: [number, number] }) => api.addPlanObject(projectId, { kind, x: coordinate[0], y: coordinate[1] }), onSuccess: async () => { await refresh(); setTool('select'); } });
  const moveObject = useMutation({ mutationFn: ({ id, coordinate }: { id: string; coordinate: [number, number] }) => api.updatePlanObject(projectId, id, { x: coordinate[0], y: coordinate[1] }), onSuccess: async () => { await refresh(); setTool('select'); } });
  const deleteObjects = useMutation({ mutationFn: (ids: string[]) => api.deletePlanObjects(projectId, ids), onSuccess: async () => { setSelectedIds([]); await refresh(); } });
  const undoChange = useMutation({ mutationFn: () => api.undoPlanChange(projectId), onSuccess: async () => { setSelectedIds([]); setTool('select'); await refresh(); } });
  const redoChange = useMutation({ mutationFn: () => api.redoPlanChange(projectId), onSuccess: async () => { setSelectedIds([]); setTool('select'); await refresh(); } });
  const exportPlan = useMutation({ mutationFn: () => api.createExport(projectId), onSuccess: setExportArtifact });
  const busy = addObject.isPending || moveObject.isPending || deleteObjects.isPending || undoChange.isPending || redoChange.isPending;
  // Export captures one durable version of the plan. Do not let a normal map
  // click race that snapshot and surface an avoidable version conflict.
  const editorBusy = busy || exportPlan.isPending;

  const reloadAfterConflict = useCallback(async () => {
    // React Query keeps a mutation error until it is reset. Reloading only
    // the project data would therefore leave the old conflict notice mounted
    // and the editor in the stale add/move mode.
    createManualPlan.reset();
    addObject.reset();
    moveObject.reset();
    deleteObjects.reset();
    undoChange.reset();
    redoChange.reset();
    exportPlan.reset();
    mapEditInFlightRef.current = false;
    placementRequestRef.current += 1;
    placementAbortRef.current?.abort();
    if (placementTimerRef.current !== undefined) window.clearTimeout(placementTimerRef.current);
    placementTimerRef.current = undefined;
    setPlacementCheck(undefined);
    setCursor(undefined);
    setSelectedIds([]);
    setTool('select');
    await refresh();
  }, [addObject, createManualPlan, deleteObjects, exportPlan, moveObject, redoChange, refresh, undoChange]);

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
    if ((planLocked || sourcePreview) && ['add_tree', 'add_shrub', 'move', 'draw_area'].includes(nextTool)) return;
    if (!projectHasPlan && ['add_tree', 'add_shrub', 'move'].includes(nextTool)) return;
    setTool((current) => current === nextTool && nextTool !== 'select' ? 'select' : nextTool);
  }, [editorBusy, planLocked, projectHasPlan, sourcePreview]);

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
    if (tool === 'move' && selectedId && !mapEditInFlightRef.current) {
      mapEditInFlightRef.current = true;
      moveObject.mutate({ id: selectedId, coordinate }, { onSettled: () => { mapEditInFlightRef.current = false; } });
    }
  };

  useEffect(() => {
    const handleHistoryShortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select, [contenteditable="true"]')) return;
      const modifier = event.metaKey || event.ctrlKey;
      if (!modifier || planLocked || editorBusy) return;
      if (event.key.toLowerCase() === 'z' && !event.shiftKey && historyQuery.data?.can_undo && !undoChange.isPending) {
        event.preventDefault();
        undoChange.mutate();
      }
      if ((event.key.toLowerCase() === 'y' || (event.key.toLowerCase() === 'z' && event.shiftKey)) && historyQuery.data?.can_redo && !redoChange.isPending) {
        event.preventDefault();
        redoChange.mutate();
      }
    };
    window.addEventListener('keydown', handleHistoryShortcut);
    return () => window.removeEventListener('keydown', handleHistoryShortcut);
  }, [editorBusy, historyQuery.data?.can_redo, historyQuery.data?.can_undo, planLocked, redoChange, undoChange]);

  if (projectQuery.isLoading) return <div className="app-shell"><AppHeader /><main className="center-status"><Progress label="Загрузка рабочей области" /></main></div>;
  if (!project) return <div className="app-shell"><AppHeader /><main className="center-status"><InlineMessage tone="error">{message(projectQuery.error)}</InlineMessage></main></div>;

  const operationError = createManualPlan.error ?? addObject.error ?? moveObject.error ?? deleteObjects.error ?? undoChange.error ?? redoChange.error ?? exportPlan.error ?? mapGeometryQuery.error;
  const showMapStatus = Boolean(
    (placementCheck && (tool === 'add_tree' || tool === 'add_shrub'))
    || mapGeometryQuery.isFetching
    || mapGeometryMetadata?.truncated,
  );

  return <div className="app-shell workspace-screen">
    <AppHeader workspace projectName={project.name} onReview={project.plan ? () => { setPanel('issues'); openRightPanel(); } : undefined} onExport={project.plan ? () => { setPanel('export'); openRightPanel(); } : undefined} exporting={exportPlan.isPending} onUndo={!planLocked && historyQuery.data?.can_undo ? () => undoChange.mutate() : undefined} onRedo={!planLocked && historyQuery.data?.can_redo ? () => redoChange.mutate() : undefined} undoLabel={historyQuery.data?.undo_label} redoLabel={historyQuery.data?.redo_label} historyBusy={undoChange.isPending || redoChange.isPending} actionsDisabled={editorBusy || createManualPlan.isPending} />
    <main className={`workspace-layout ${leftOpen ? '' : 'is-left-collapsed'} ${rightOpen ? '' : 'is-right-collapsed'}`}>
      <aside className="workspace-left"><ProjectLayers layers={layers} visibility={visibility} activeLayerId={activeLayerId} onVisibility={(id, visible) => setVisibility((current) => ({ ...current, [id]: visible }))} onSelect={(id) => { setActiveLayerId((current) => current === id ? undefined : id); setSelectedIds([]); setPanel(null); openRightPanel(); }} onClose={() => setLeftOpen(false)} /></aside>
      <aside className="workspace-left-collapsed"><IconButton icon={ChevronRight} label="Развернуть слои" variant="ghost" onClick={openLeftPanel} /><IconButton icon={Layers3} label="Слои" active onClick={openLeftPanel} /></aside>
      <section className={`map-canvas ${rightOpen ? '' : 'has-right-dock'}`}>
        <MapViewport key={projectId} ref={mapViewport} geometry={mapGeometryQuery.data?.feature_collection as Record<string, unknown> | undefined} geometryRevision={project.geometry_version} initialExtent={initialExtent} objects={planObjects} draftPlantingZones={!projectHasPlan ? draftZones : undefined} hiddenLayerNames={hiddenLayerNames} selectedIds={selectedIds} placementPreview={placementPreview} tool={tool} onDrawArea={(geometry) => {
          if (projectHasPlan || planLocked) return;
          addMapArea(geometry, `Ручной участок ${draftZones.length + 1}`);
          setTool('select');
        }} onMapArea={({ sourceId, geometry, label }) => {
          if (!projectHasPlan) addMapArea(geometry, label, sourceId);
        }} onMapHover={setMapHoverTarget} onSelect={(id, additive) => {
          if (!projectHasPlan) return;
          if (!id) { if (!additive) setSelectedIds([]); return; }
          setSelectedIds((current) => additive ? (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]) : [id]);
          setActiveLayerId(undefined);
          setPanel(null);
          openRightPanel();
        }} onCoordinate={handleCoordinate} onPointerCoordinate={handlePointerCoordinate} onExtentChange={handleMapExtent} />
        {mapHoverTarget && tool === 'select' && !projectHasPlan ? <div className={`map-hover-hint ${mapHoverTarget.pixel[0] > 520 ? 'is-left' : ''}`} style={{ left: mapHoverTarget.pixel[0] + 14, top: mapHoverTarget.pixel[1] + 14 }} role="status"><strong>{mapHoverTarget.label}</strong><span>{mapHoverTarget.detail}</span></div> : null}
        {projectHasPlan ? <div className="map-edit-tools"><MapToolbar tool={tool} onTool={activateTool} editable={!sourcePreview && !planLocked && !editorBusy} canDelete={selectedIds.length > 0} onDelete={() => setDeleteSelectionOpen(true)} /></div> : null}
        <div className="map-zoom-tools"><IconButton icon={Plus} label="Увеличить" variant="ghost" onClick={() => mapViewport.current?.zoomIn()} /><IconButton icon={Minus} label="Уменьшить" variant="ghost" onClick={() => mapViewport.current?.zoomOut()} /><IconButton icon={Maximize2} label="Показать весь чертёж" variant="ghost" onClick={() => mapViewport.current?.fit()} /></div>
        {project.plan ? <div className="map-plan-focus"><IconButton icon={Crosshair} label="Показать посадки" variant="ghost" onClick={() => mapViewport.current?.fitPlan()} /></div> : null}
        {!rightOpen ? <div className="right-dock"><IconButton icon={ChevronLeft} label="Развернуть панель" variant="ghost" onClick={openRightPanel} /><IconButton icon={project.plan ? AlertTriangle : Scan} label={project.plan ? 'Проверка' : 'Участки'} variant="ghost" onClick={() => { setPanel(project.plan ? 'issues' : 'zones'); openRightPanel(); }} /></div> : null}
        {showMapStatus ? <div className="map-statusbar">{placementCheck && (tool === 'add_tree' || tool === 'add_shrub') ? <span className={`placement-check placement-check--${placementCheck.status}`} role="status">{placementCheck.reason}</span> : null}{mapGeometryQuery.isFetching ? <span className="map-stream-status">Обновляем карту</span> : null}{mapGeometryMetadata?.truncated ? <span className="map-lod-warning" role="status">Приблизьте карту, чтобы увидеть детали.</span> : null}</div> : null}
        {busy ? <div className="map-busy"><Progress label="Сохраняем изменения" /></div> : null}
        {operationError ? <div className="map-operation-error">{isProjectConflict(operationError) ? <ProjectConflictNotice error={operationError} onReload={() => void reloadAfterConflict()} reloading={projectQuery.isFetching} /> : <InlineMessage tone="error">{message(operationError)}</InlineMessage>}</div> : null}
      </section>
      <aside className="workspace-right">
        {!panel ? <button className="right-close" type="button" aria-label="Свернуть инспектор" onClick={closeRightPanel}><PanelRightClose size={16} /></button> : null}
        {panel === 'zones' && !project.plan ? <div className="workspace-zones-panel"><button className="right-close" type="button" aria-label="Свернуть панель участков" onClick={closeRightPanel}><PanelRightClose size={16} /></button><PlantingZonesPanel assignments={draftZones} drawingManual={tool === 'draw_area'} saving={createManualPlan.isPending} error={createManualPlan.error ? message(createManualPlan.error) : undefined} onRemove={(id) => setDraftZones((current) => current.filter((item) => item.id !== id))} onSave={() => createManualPlan.mutate()} onManual={() => setTool('draw_area')} onCancelManual={() => setTool('select')} /></div> : null}
        {panel === 'issues' ? <div className="rail-panel"><RailPanelHeader title="Проверка плана" subtitle={issueCount ? `${issueCount} замечания` : 'Нарушений нет'} onBack={returnToInspector} onClose={closeRightPanel} /><ValidationPanel issues={issues} onLocate={(id) => { setSelectedIds(id ? [id] : []); setTool('select'); setPanel(null); if (id) requestAnimationFrame(() => mapViewport.current?.fitSelection(id)); }} /></div> : null}
        {panel === 'export' && project.plan ? <div className="rail-panel export-panel"><RailPanelHeader title="Экспорт DXF" subtitle="Исходный чертёж и посадки" onBack={returnToInspector} onClose={closeRightPanel} /><div className="rail-panel__content"><ExportArtifactSection title="План в DXF" description="Исходный чертёж без потери данных и слой посадок." artifact={exportArtifact} icon={Layers3} loading={exportPlan.isPending} prepareLabel="Подготовить DXF" downloadLabel="Скачать DXF" readyLabel="Файл готов" onPrepare={() => exportPlan.mutate()} onDownload={(artifact) => { window.location.href = api.downloadUrl(artifact.download_url); }} /></div></div> : null}
        {!panel && activeLayer ? <LayerInspector layer={activeLayer} visible={visibility[activeLayer.id] !== false} onVisibility={(visible) => setVisibility((current) => ({ ...current, [activeLayer.id]: visible }))} onFit={() => mapViewport.current?.fitLayer(activeLayer.source_name)} /> : null}
        {!panel && !activeLayer && sourcePreview ? <div className="project-inspector"><header><span><strong>Исходный DXF</strong><small>Только просмотр</small></span></header><div className="project-inspector__empty"><Layers3 size={20} /><strong>Проверьте слои и геометрию</strong><span>Подтвердите слои, затем выберите или обведите рабочую область на карте.</span>{sourceWarnings.map((warning) => <InlineMessage key={warning} tone="warning">{warning}</InlineMessage>)}<Button variant="secondary" onClick={openLeftPanel}>Открыть слои</Button><Button variant="primary" onClick={() => navigate(`/projects/${projectId}/setup`)}>К сопоставлению</Button></div></div> : null}
        {!panel && !activeLayer && selectedObject ? <ObjectInspector object={selectedObject} editable={!planLocked && !editorBusy} onMove={() => setTool('move')} onDelete={() => setDeleteSelectionOpen(true)} /> : null}
        {!panel && !activeLayer && selectedIds.length > 1 ? <div className="project-inspector multi-selection-inspector"><header><span><strong>Выбрано посадок</strong><small>{selectedIds.length} объектов</small></span></header><section>{!planLocked ? <Button variant="danger" icon={Trash2} disabled={editorBusy} onClick={() => setDeleteSelectionOpen(true)}>Удалить выбранные</Button> : null}</section></div> : null}
        {!panel && !activeLayer && !selectedIds.length && project.plan ? <div className="project-inspector"><header><span><strong>Ручной план</strong><small>{planObjects.length} посадок</small></span></header><section className="plan-summary"><strong>Поставьте или выберите посадку</strong><span>Проверка появится сразу.</span>{sourceWarnings.length ? <InlineMessage tone="warning">У исходного DXF есть замечания.</InlineMessage> : null}</section><section className="project-inspector__actions"><Button variant="secondary" icon={Crosshair} onClick={() => mapViewport.current?.fitPlan()}>Показать посадки</Button></section></div> : null}
      </aside>
      <Dialog open={deleteSelectionOpen} title={selectedIds.length > 1 ? `Удалить ${selectedIds.length} посадок?` : 'Удалить посадку?'} onClose={() => setDeleteSelectionOpen(false)} footer={<><Button variant="secondary" disabled={editorBusy} onClick={() => setDeleteSelectionOpen(false)}>Отмена</Button><Button variant="danger" icon={Trash2} loading={deleteObjects.isPending} disabled={exportPlan.isPending} onClick={() => deleteObjects.mutate(selectedIds, { onSuccess: () => setDeleteSelectionOpen(false) })}>Удалить</Button></>}><p>Посадки будут убраны из текущей схемы. Это действие можно отменить.</p></Dialog>
    </main>
  </div>;
}
