import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { SceneSnapshot, ValidationIssue } from '@green/api-client';
import { Checkbox, IconButton, InlineMessage } from '@green/ui';
import { Focus, ScanSearch } from 'lucide-react';
import { GrowthHorizonSlider } from './GrowthHorizonControl';
import { PlantAssetLibrary } from './scene/plantAssets';
import { SceneController, type SceneHover } from './scene/SceneController';
import {
  SCENE_ACCESSIBILITY_CONTRACT,
  SCENE_RENDERER_LIFECYCLE_KEY,
  SCENE_TELEMETRY_ATTRIBUTES,
  type SceneRenderTelemetry,
} from './scene/sceneRenderContract';

type HoverPresentation = {
  objectId: string;
  x: number;
  y: number;
};

const growthLabels: Record<string, string> = {
  planting: 'Посадочный размер',
  young: 'Молодое растение',
  developing: 'Формирующаяся крона',
  mature: 'Зрелая форма',
};

export function SceneReview({ snapshot, horizon, selectedIds, issues = [], loading, error, onHorizon, onSelect }: {
  snapshot?: SceneSnapshot;
  horizon: number;
  selectedIds: string[];
  issues?: ValidationIssue[];
  loading?: boolean;
  error?: string;
  onHorizon: (horizon: number) => void;
  onSelect: (objectId: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<SceneController | undefined>(undefined);
  const latestRef = useRef({ snapshot, selectedIds, issues });
  const onSelectRef = useRef(onSelect);
  const [showRoots, setShowRoots] = useState(false);
  const [renderError, setRenderError] = useState<string>();
  const [modelsReady, setModelsReady] = useState(false);
  const [telemetry, setTelemetry] = useState<SceneRenderTelemetry>({
    rendererGeneration: 0,
    fps: 0,
    drawCalls: 0,
    triangles: 0,
    plantInstances: snapshot?.objects.length ?? 0,
    visiblePlantInstances: 0,
  });
  const [hover, setHover] = useState<HoverPresentation>();
  latestRef.current = { snapshot, selectedIds, issues };
  onSelectRef.current = onSelect;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let active = true;
    let controller: SceneController | undefined;
    void PlantAssetLibrary.load().then((assetLibrary) => {
      if (!active) {
        assetLibrary.dispose();
        return;
      }
      try {
        controller = new SceneController({
          canvas,
          assetLibrary,
          onSelect: (objectId) => onSelectRef.current(objectId),
          onHover: (next: SceneHover) => {
            if (!next.objectId) {
              setHover(undefined);
              return;
            }
            const rect = canvas.getBoundingClientRect();
            setHover({ objectId: next.objectId, x: next.clientX - rect.left, y: next.clientY - rect.top });
          },
          onTelemetry: setTelemetry,
        });
        controllerRef.current = controller;
        const latest = latestRef.current;
        if (latest.snapshot) controller.updateSnapshot(latest.snapshot, latest.issues);
        controller.updateSelection(latest.selectedIds);
        setModelsReady(true);
        setRenderError(undefined);
      } catch {
        assetLibrary.dispose();
        setRenderError('3D недоступен в этом браузере. План остаётся доступен в 2D.');
      }
    });
    return () => {
      active = false;
      controller?.dispose();
      if (controllerRef.current === controller) controllerRef.current = undefined;
    };
  }, []);

  useEffect(() => {
    if (snapshot) controllerRef.current?.updateSnapshot(snapshot, latestRef.current.issues);
  }, [snapshot]);

  useEffect(() => {
    controllerRef.current?.updateIssues(issues);
  }, [issues]);

  useEffect(() => {
    controllerRef.current?.updateSelection(selectedIds);
  }, [selectedIds]);

  useEffect(() => {
    controllerRef.current?.setRootsVisible(showRoots);
  }, [showRoots]);

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
    : 'Высоты не заданы';
  const telemetryProps = {
    [SCENE_TELEMETRY_ATTRIBUTES.rendererGeneration]: telemetry.rendererGeneration,
    [SCENE_TELEMETRY_ATTRIBUTES.fps]: telemetry.fps,
    [SCENE_TELEMETRY_ATTRIBUTES.drawCalls]: telemetry.drawCalls,
    [SCENE_TELEMETRY_ATTRIBUTES.triangles]: telemetry.triangles,
    [SCENE_TELEMETRY_ATTRIBUTES.plantInstances]: telemetry.plantInstances,
    [SCENE_TELEMETRY_ATTRIBUTES.visiblePlantInstances]: telemetry.visiblePlantInstances,
  };

  return <section
    className="scene-review"
    aria-label="Параметрический 3D-предпросмотр"
    data-renderer-lifecycle={SCENE_RENDERER_LIFECYCLE_KEY}
    {...telemetryProps}
  >
    <header className="scene-review__header">
      <div className="scene-review__identity">
        <strong>Объёмная модель</strong>
        <small>{sourceStatus} · {heightStatus}</small>
      </div>
      <div className="scene-review__basis" aria-label="Достоверность модели">
        <span>DXF — источник</span>
        <small>{snapshot?.terrain_status === 'confirmed' ? 'Рельеф подтверждён' : 'Плоская опорная поверхность'}</small>
      </div>
      <div className="scene-review__horizons" aria-label="Горизонт роста">
        <small>{horizon === 0 ? 'Сейчас' : `${horizon} лет`}</small>
        <GrowthHorizonSlider value={horizon} onChange={(next) => { if (next !== undefined) onHorizon(next); }} />
      </div>
      <div className="scene-review__view-actions" aria-label="Камера 3D">
        <IconButton icon={ScanSearch} label={SCENE_ACCESSIBILITY_CONTRACT.resetCameraLabel} variant="ghost" controlSize="compact" onClick={() => controllerRef.current?.setCameraMode('overview')} />
        <IconButton icon={Focus} label={SCENE_ACCESSIBILITY_CONTRACT.focusSelectionLabel} variant="ghost" controlSize="compact" disabled={!selectedIds.length} onClick={() => controllerRef.current?.setCameraMode('ground')} />
      </div>
      <Checkbox label="Корни" checked={showRoots} onChange={(event) => setShowRoots(event.target.checked)} />
    </header>
    <div className="scene-review__viewport">
      <canvas ref={canvasRef} aria-label={SCENE_ACCESSIBILITY_CONTRACT.canvasLabel} />
      {(!modelsReady || loading) && !renderError ? <div className="scene-review__status">{modelsReady ? 'Обновляем прогноз' : 'Загружаем модели растений'}</div> : null}
      {error || renderError ? <div className="scene-review__message"><InlineMessage tone="error">{error ?? renderError}</InlineMessage></div> : null}
      {hover && hoveredObject ? <div
        className={`scene-review__hover-card ${hoveredIssue ? `is-${hoveredIssue.severity}` : ''}`}
        style={{ '--scene-hover-x': `${hover.x + 18}px`, '--scene-hover-y': `${hover.y + 18}px` } as CSSProperties}
        aria-hidden="true"
      >
        <strong>{hoveredObject.common_name ?? (hoveredObject.kind === 'tree' ? 'Дерево' : 'Кустарник')}</strong>
        <span>{growthLabels[hoveredObject.growth_stage] ?? 'Стадия не определена'} · {hoveredObject.forecast_horizon_year ? `через ${hoveredObject.forecast_horizon_year} лет` : 'сейчас'}</span>
        {hoveredIssue ? <small>{hoveredIssue.severity === 'error' ? 'Ошибка' : 'Риск'}: {hoveredIssue.title}</small> : null}
      </div> : null}
      <div className="scene-review__outline-key" aria-label={SCENE_ACCESSIBILITY_CONTRACT.outlineDescription}>
        <span><i className="is-selected" /> выбор</span>
        <span><i className="is-warning" /> риск</span>
        <span><i className="is-error" /> ошибка</span>
      </div>
    </div>
    <footer>
      <span>{snapshot?.objects.length ?? 0} посадок</span>
      <span>Выбрано: {selectedIds.length}</span>
      <span className="scene-review__legend"><i className="is-road" /> дороги <i className="is-building" /> здания <i className="is-water" /> вода</span>
    </footer>
  </section>;
}
