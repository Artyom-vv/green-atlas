import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { PlantingZoneAssignment, SceneSnapshot, ValidationIssue } from '@green/api-client';
import { Button, Checkbox, Dialog, IconButton, InlineMessage } from '@green/ui';
import { Focus, Info, Minus, Plus, ScanSearch } from 'lucide-react';
import { GrowthHorizonControl } from './GrowthHorizonControl';
import './scene-review.css';
import { acquirePlantAssetLibrary } from './scene/plantAssets';
import { createSceneRenderer, SceneController, type SceneHover } from './scene/SceneController';
import {
  SCENE_ACCESSIBILITY_CONTRACT,
  SCENE_RENDERER_LIFECYCLE_KEY,
  SCENE_TELEMETRY_ATTRIBUTES,
  type SceneRenderTelemetry,
} from './scene/sceneRenderContract';
import { registerSceneRenderer } from './scene/sceneTelemetryRegistry';
import type { PlanViewState } from './planViewState';

type HoverPresentation = {
  objectId: string;
  x: number;
  y: number;
};

const EMPTY_ZONES: PlantingZoneAssignment[] = [];
const EMPTY_IDS: string[] = [];

const growthLabels: Record<string, string> = {
  planting: 'Посадочный размер',
  young: 'Молодое растение',
  developing: 'Формирующаяся крона',
  mature: 'Зрелая форма',
};

export type SceneReviewHandle = {
  getViewState: () => PlanViewState | undefined;
  fitPlantings: () => void;
  fitSelection: () => void;
  fitExtent: (extent: readonly number[]) => void;
};

export const SceneReview = forwardRef<SceneReviewHandle, {
  snapshot?: SceneSnapshot;
  zones?: PlantingZoneAssignment[];
  selectedZoneIds?: string[];
  horizon: number;
  showGrowthControl?: boolean;
  selectedIds: string[];
  issues?: ValidationIssue[];
  active?: boolean;
  loading?: boolean;
  error?: string;
  onHorizon: (horizon: number) => void;
  onSelect: (objectId: string) => void;
  onMoveTarget?: (coordinate: [number, number]) => void;
  initialViewState?: PlanViewState;
  onViewStateChange?: (state: PlanViewState) => void;
}>(function SceneReview({ snapshot, zones = EMPTY_ZONES, selectedZoneIds = EMPTY_IDS, horizon, showGrowthControl = true, selectedIds, issues = [], active = true, loading, error, onHorizon, onSelect, onMoveTarget, initialViewState, onViewStateChange }, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<SceneController | undefined>(undefined);
  const latestRef = useRef({ snapshot, selectedIds, issues, zones, selectedZoneIds });
  const onSelectRef = useRef(onSelect);
  const onMoveTargetRef = useRef(onMoveTarget);
  const initialViewStateRef = useRef(initialViewState);
  const onViewStateChangeRef = useRef(onViewStateChange);
  const [showRoots, setShowRoots] = useState(false);
  const [renderError, setRenderError] = useState<string>();
  const [modelsReady, setModelsReady] = useState(false);
  const [telemetry, setTelemetry] = useState<SceneRenderTelemetry>();
  const [hover, setHover] = useState<HoverPresentation>();
  const [dataOpen, setDataOpen] = useState(false);
  const [rendererAttempt, setRendererAttempt] = useState(0);
  latestRef.current = { snapshot, selectedIds, issues, zones, selectedZoneIds };
  onSelectRef.current = onSelect;
  onMoveTargetRef.current = onMoveTarget;
  onViewStateChangeRef.current = onViewStateChange;
  useImperativeHandle(ref, () => ({
    getViewState: () => controllerRef.current?.exportPlanViewState(),
    fitPlantings: () => controllerRef.current?.fitPlantings(),
    fitSelection: () => controllerRef.current?.fitSelection(),
    fitExtent: (extent) => controllerRef.current?.fitExtent(extent),
  }), []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let renderer;
    try {
      renderer = createSceneRenderer(canvas);
    } catch (caught) {
      console.error('Green Atlas could not create a WebGL renderer', caught);
      setRenderError('3D недоступен в этом браузере. План остаётся доступен в 2D.');
      return;
    }
    let active = true;
    let controller: SceneController | undefined;
    let rendererRegistration: ReturnType<typeof registerSceneRenderer> | undefined;
    const assetLease = acquirePlantAssetLibrary('/assets/plant-models/manifest.json', { renderer });
    void assetLease.library.then((assetLibrary) => {
      if (!active) return;
      try {
        if (!assetLibrary.loadedModelCount()) throw new Error('No reviewed plant models could be loaded');
        controller = new SceneController({
          canvas,
          assetLibrary,
          renderer,
          onSelect: (objectId) => onSelectRef.current(objectId),
          onMoveTarget: (coordinate) => {
            onMoveTargetRef.current?.(coordinate);
          },
          onHover: (next: SceneHover) => {
            if (!next.objectId) {
              setHover(undefined);
              return;
            }
            const rect = canvas.getBoundingClientRect();
            setHover({ objectId: next.objectId, x: next.clientX - rect.left, y: next.clientY - rect.top });
          },
          onTelemetry: (next) => {
            setTelemetry(next);
            rendererRegistration?.publish(next);
          },
          initialViewState: initialViewStateRef.current,
          onViewStateChange: (state) => onViewStateChangeRef.current?.(state),
        });
        rendererRegistration = registerSceneRenderer();
        controllerRef.current = controller;
        const latest = latestRef.current;
        if (latest.snapshot) controller.updateSnapshot(latest.snapshot, latest.issues);
        controller.updateSelection(latest.selectedIds);
        controller.updateWorkZones(latest.zones, latest.selectedZoneIds);
        setModelsReady(true);
        setRenderError(undefined);
      } catch (caught) {
        console.error('Green Atlas could not initialise the 3D scene', caught);
        renderer.dispose();
        setRenderError('Не удалось загрузить 3D-сцену. 2D-план сохранён.');
      }
    });
    return () => {
      active = false;
      if (controller) controller.dispose();
      else {
        // React's development probe immediately remounts this effect on the
        // same canvas. Releasing GPU allocations is sufficient here; forcing
        // context loss would poison the canvas before the real mount.
        renderer.dispose();
      }
      rendererRegistration?.dispose();
      assetLease.release();
      if (controllerRef.current === controller) controllerRef.current = undefined;
    };
  }, [rendererAttempt]);

  useEffect(() => {
    if (snapshot) controllerRef.current?.updateSnapshot(snapshot, latestRef.current.issues);
  }, [snapshot]);

  useEffect(() => {
    controllerRef.current?.updateIssues(issues);
  }, [issues]);

  useEffect(() => {
    controllerRef.current?.updateSelection(selectedIds);
  }, [selectedIds]);

  useEffect(() => { controllerRef.current?.updateWorkZones(zones, selectedZoneIds); }, [zones, selectedZoneIds, snapshot, modelsReady]);

  useEffect(() => {
    controllerRef.current?.setRootsVisible(showRoots);
  }, [showRoots]);

  useEffect(() => {
    if (active && initialViewState) controllerRef.current?.importPlanViewState(initialViewState);
  }, [active, initialViewState]);

  useEffect(() => {
    if (!active) setHover(undefined);
  }, [active]);

  const hoveredObject = useMemo(
    () => hover ? snapshot?.objects.find((object) => object.object_id === hover.objectId) : undefined,
    [hover, snapshot],
  );
  const hoveredIssue = useMemo(
    () => hoveredObject ? issues.find((issue) => issue.object_id === hoveredObject.object_id) : undefined,
    [hoveredObject, issues],
  );
  const sourceStatus = snapshot?.georeference_status === 'confirmed'
    ? 'Геопривязка подтверждена'
    : snapshot?.georeference_status === 'declared'
      ? 'Геопривязка заявлена'
      : 'Локальные координаты DXF';
  const heightStatus = snapshot?.building_heights_status === 'confirmed'
    ? `${snapshot.building_height_confirmed_count} высот из DXF`
    : snapshot?.building_heights_status === 'estimated'
      ? 'Высоты OSM и оценки по этажности'
      : 'Высоты не заданы';
  const telemetryProps = telemetry ? {
    [SCENE_TELEMETRY_ATTRIBUTES.status]: 'ready',
    [SCENE_TELEMETRY_ATTRIBUTES.rendererGeneration]: telemetry.rendererGeneration,
    [SCENE_TELEMETRY_ATTRIBUTES.fps]: telemetry.fps,
    [SCENE_TELEMETRY_ATTRIBUTES.drawCalls]: telemetry.drawCalls,
    [SCENE_TELEMETRY_ATTRIBUTES.triangles]: telemetry.triangles,
    [SCENE_TELEMETRY_ATTRIBUTES.renderScale]: telemetry.renderScale,
    [SCENE_TELEMETRY_ATTRIBUTES.effectivePixelRatio]: telemetry.effectivePixelRatio,
    [SCENE_TELEMETRY_ATTRIBUTES.outlineWidthBufferPx]: telemetry.outlineWidthBufferPx,
    [SCENE_TELEMETRY_ATTRIBUTES.geometries]: telemetry.geometries,
    [SCENE_TELEMETRY_ATTRIBUTES.textures]: telemetry.textures,
    [SCENE_TELEMETRY_ATTRIBUTES.programs]: telemetry.programs,
    [SCENE_TELEMETRY_ATTRIBUTES.loadedPlantModels]: telemetry.loadedPlantModels,
    [SCENE_TELEMETRY_ATTRIBUTES.plantInstances]: telemetry.plantInstances,
    [SCENE_TELEMETRY_ATTRIBUTES.visiblePlantInstances]: telemetry.visiblePlantInstances,
  } : {
    [SCENE_TELEMETRY_ATTRIBUTES.status]: 'warming-up',
    [SCENE_TELEMETRY_ATTRIBUTES.rendererGeneration]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.fps]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.drawCalls]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.triangles]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.renderScale]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.effectivePixelRatio]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.outlineWidthBufferPx]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.geometries]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.textures]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.programs]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.loadedPlantModels]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.plantInstances]: 0,
    [SCENE_TELEMETRY_ATTRIBUTES.visiblePlantInstances]: 0,
  };

  return <section
    className="scene-review"
    aria-label="Параметрический 3D-предпросмотр"
    hidden={!active}
    data-renderer-lifecycle={SCENE_RENDERER_LIFECYCLE_KEY}
    {...telemetryProps}
  >
    {telemetry ? <output className="sr-only" aria-hidden="true">
      {`${telemetry.fps} FPS, ${telemetry.drawCalls} draw calls, ${telemetry.triangles} triangles, render scale ${telemetry.renderScale}, DPR ${telemetry.effectivePixelRatio}`}
    </output> : null}
    <div className="scene-camera-controls" role="group" aria-label="Камера 3D">
      <IconButton icon={Plus} label="Приблизить 3D" variant="ghost" onClick={() => controllerRef.current?.zoom(.8)} />
      <IconButton icon={Minus} label="Отдалить 3D" variant="ghost" onClick={() => controllerRef.current?.zoom(1.25)} />
      <IconButton icon={ScanSearch} label="Показать весь чертёж в 3D" variant="ghost" onClick={() => controllerRef.current?.setCameraMode('overview')} />
      <IconButton icon={Focus} label="Показать выбранные посадки" variant="ghost" disabled={!selectedIds.length} onClick={() => controllerRef.current?.fitSelection()} />
    </div>
    <div className="scene-growth-control" role="group" aria-label="Возраст посадок в 3D" hidden={!showGrowthControl}>
      <GrowthHorizonControl value={horizon} showMetrics={false} onChange={next => { if (next !== undefined) onHorizon(next); }} />
    </div>
    <IconButton className="scene-data-button" icon={Info} label="Данные и управление 3D" variant="secondary" onClick={() => setDataOpen(true)} />
    <Dialog open={dataOpen} title="Данные сцены" onClose={() => setDataOpen(false)}>
      <dl className="editor-panel__metrics"><dt>Координаты</dt><dd>{sourceStatus}</dd><dt>Высоты зданий</dt><dd>{heightStatus}</dd><dt>Рельеф</dt><dd>{snapshot?.terrain_status === 'confirmed' ? 'Подтверждён в DXF' : snapshot?.terrain_status === 'estimated' ? 'DEM, оценочные высоты' : 'Высоты не заданы'}</dd><dt>Рабочие участки</dt><dd>{zones.length}</dd><dt>Посадки</dt><dd>{snapshot?.objects.length ?? 0}</dd></dl>
      <Checkbox label="Показать корни" checked={showRoots} onChange={event => setShowRoots(event.target.checked)} />
      <p>Перетаскивание — перемещение карты. Правая кнопка — поворот. Колесо — масштаб.</p>
      <p>Синий контур — выбор, оранжевый — риск, красный — конфликт. Посадки редактируются в 2D.</p>
    </Dialog>
    <div className="scene-review__viewport">
      <canvas ref={canvasRef} aria-label={SCENE_ACCESSIBILITY_CONTRACT.canvasLabel} />
      {(!modelsReady || loading) && !renderError ? <div className="scene-review__status">{modelsReady ? 'Обновляем прогноз' : 'Загружаем модели растений'}</div> : null}
      {error || renderError ? <div className="scene-review__message">
        <InlineMessage tone="error">{error ?? renderError}</InlineMessage>
        {renderError ? <Button variant="secondary" onClick={() => {
          setRenderError(undefined);
          setModelsReady(false);
          setRendererAttempt((attempt) => attempt + 1);
        }}>Повторить запуск 3D</Button> : null}
      </div> : null}
      {hover && hoveredObject ? <div
        className={`scene-review__hover-card ${hoveredIssue ? `is-${hoveredIssue.severity}` : ''}`}
        style={{ '--scene-hover-x': `${hover.x + 18}px`, '--scene-hover-y': `${hover.y + 18}px` } as CSSProperties}
        aria-hidden="true"
      >
        <strong>{hoveredObject.common_name ?? (hoveredObject.kind === 'tree' ? 'Дерево' : 'Кустарник')}</strong>
        <span>{growthLabels[hoveredObject.growth_stage] ?? 'Стадия не определена'}</span><span>{hoveredObject.forecast_horizon_year ? `Через ${hoveredObject.forecast_horizon_year} лет` : 'Сейчас'}</span>
        {hoveredIssue ? <small>{hoveredIssue.severity === 'error' ? 'Ошибка' : 'Риск'}: {hoveredIssue.title}</small> : null}
      </div> : null}
    </div>
  </section>;
});
