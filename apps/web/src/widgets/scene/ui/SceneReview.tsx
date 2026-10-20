import { ControlProvider, Divider, IconButton } from '@green/ui';
import { Camera, CircleAlert } from 'lucide-react';
import {
  CanvasControlGroup,
  CanvasControlSlot,
} from '@/shared/ui/CanvasControls';
import type { SceneRenderPreparation } from '@green/api-client';
import type { StreetCameraPlacementState } from '../model/streetCameraPlacement';
import { preparationMatchesSnapshot } from '../adapters/three/SceneEnvironmentLayers';
import { SceneStreetPlacementControl } from './SceneStreetPlacementControl';
import { useEffect, useState, type FC, type Ref, type ReactNode } from 'react';
import { useSceneActivity } from '../model/SceneActivityContext';
import { createPortal } from 'react-dom';
import { ViewportLoading } from '@/shared/ui/ViewportLoading';
import { useSceneRender } from '@/features/scene-render/model/useSceneRender';
import { ProjectLocationDialog } from '@/features/project-location';
import { useSceneRenderer } from '@/widgets/scene/adapters/three/useSceneRenderer';
import {
  SCENE_ACCESSIBILITY_CONTRACT,
  SCENE_RENDERER_LIFECYCLE_KEY,
} from '@/widgets/scene/model/sceneRenderContract';
import type {
  SceneReviewHandle,
  SceneReviewOptions,
} from '@/widgets/scene/model/sceneContracts';
import { sceneTelemetryAttributes } from '@/widgets/scene/model/scenePresentation';
import { SceneDataDialog } from './SceneDataDialog';
import { SceneHoverCard } from './SceneHoverCard';
import { SceneCameraControls } from './SceneCameraControls';
import { SceneForecastControl } from './SceneForecastControl';
import { SceneStatus } from './SceneStatus';
import { SceneEnvironmentControl } from './SceneEnvironmentControl';
import { SceneRenderControl } from './SceneRenderControl';

export type { SceneReviewHandle } from '@/widgets/scene/model/sceneContracts';

export interface SceneReviewProps extends SceneReviewOptions {
  ref?: Ref<SceneReviewHandle>;
  viewControls?: ReactNode;
  onFitZones?: () => void;
  zoneCount?: number;
  displayEnvironment?: SceneRenderPreparation;
  nativeModels?: boolean;
}

export const SceneReview: FC<SceneReviewProps> = ({
  ref,
  viewControls,
  onFitZones,
  zoneCount,
  displayEnvironment,
  nativeModels,
  ...options
}) => {
  const {
    snapshot,
    horizon,
    selectedIds,
    active = true,
    showGrowthControl = true,
    loading,
    error,
    onHorizon,
  } = options;
  const scene = useSceneRenderer(options, ref, nativeModels);
  const activity = useSceneActivity();
  const [dataOpen, setDataOpen] = useState(false);
  const [renderOpen, setRenderOpen] = useState(false);
  const [locationOpen, setLocationOpen] = useState(false);
  const [streetPlacement, setStreetPlacement] =
    useState<StreetCameraPlacementState>();
  const streetPicking = Boolean(streetPlacement);
  const [streetView, setStreetView] = useState(false);
  const [environmentError, setEnvironmentError] = useState<string>();
  const [applied, setApplied] = useState<SceneRenderPreparation>();
  const [firstFrameReady, setFirstFrameReady] = useState(false);
  const [environmentApplying, setEnvironmentApplying] = useState(false);
  const [drawingFrame, setDrawingFrame] = useState(false);
  const displayed =
    applied && snapshot && preparationMatchesSnapshot(applied, snapshot)
      ? applied
      : undefined;
  const appliedPreparation = displayed?.id;
  const environment = {
    preparation: displayEnvironment,
    loading: false,
    startedAt: undefined,
    error: undefined as Error | undefined,
    refresh: () => options.onRetryScene?.(),
  };
  useEffect(() => {
    const controller = scene.controllerRef.current;
    if (!controller || !snapshot || loading) return;
    let cancelled = false;
    setEnvironmentApplying(true);
    setDrawingFrame(false);
    if (!environment.preparation) setApplied(undefined);
    setEnvironmentError(undefined);
    void (async () => {
      try {
        const applied = await controller.updateEnvironment(
          environment.preparation,
        );
        if (cancelled) return;
        if (environment.preparation && !applied)
          throw new Error(
            'Подготовленное окружение не совпадает с текущей 3D-сценой',
          );
        setDrawingFrame(true);
        setApplied(environment.preparation);
        setEnvironmentApplying(false);
        setEnvironmentError(undefined);
      } catch (failure) {
        if (cancelled) return;
        setEnvironmentApplying(false);
        setDrawingFrame(false);
        setEnvironmentError(
          failure instanceof Error
            ? failure.message
            : 'Не удалось показать окружение',
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    environment.preparation,
    snapshot,
    loading,
    scene.modelsReady,
    scene.controllerRef,
  ]);
  useEffect(() => {
    if (!active) {
      setFirstFrameReady(false);
      setDrawingFrame(false);
      return;
    }
    const controller = scene.controllerRef.current;
    if (
      !controller ||
      !scene.modelsReady ||
      !snapshot ||
      loading ||
      environmentApplying ||
      (displayEnvironment && appliedPreparation !== displayEnvironment.id)
    )
      return;
    let cancelled = false;
    setDrawingFrame(true);
    void controller.whenRendered().then((rendered) => {
      if (cancelled) return;
      if (rendered) setFirstFrameReady(true);
      setDrawingFrame(false);
    });
    return () => {
      cancelled = true;
    };
  }, [
    active,
    scene.modelsReady,
    scene.controllerRef,
    snapshot,
    loading,
    environmentApplying,
    displayEnvironment,
    appliedPreparation,
  ]);
  useEffect(() => {
    setStreetPlacement(undefined);
    scene.controllerRef.current?.cancelStreetPlacement();
  }, [
    snapshot?.project_id,
    snapshot?.content_sha256,
    snapshot?.geometry_version,
    snapshot?.plan_version,
    active,
    horizon,
    loading,
    error,
    scene.renderError,
    scene.controllerRef,
  ]);
  useEffect(() => {
    setStreetView(false);
  }, [
    snapshot?.project_id,
    snapshot?.geometry_version,
    active,
    scene.renderError,
  ]);
  const sceneReady =
    scene.modelsReady &&
    firstFrameReady &&
    !environmentApplying &&
    !drawingFrame &&
    !loading &&
    !error &&
    !scene.renderError &&
    snapshot?.horizon_year === horizon;
  const currentEnvironmentError = environment.loading
    ? undefined
    : environmentError || environment.error?.message;
  const renderOperation = useSceneRender(
    snapshot,
    () => scene.controllerRef.current?.exportRenderCamera(),
    displayed,
  );
  const sceneFailure = error || scene.renderError || currentEnvironmentError;
  const sceneLoading = Boolean(
    active &&
    !sceneFailure &&
    (loading ||
      !scene.modelsReady ||
      !firstFrameReady ||
      environmentApplying ||
      drawingFrame ||
      (displayEnvironment && appliedPreparation !== displayEnvironment.id)),
  );
  const headerLoading =
    sceneLoading ||
    Boolean(renderOperation.busy && !renderOpen && !renderOperation.error);
  const hasRender = Boolean(renderOperation.start.submittedAt);
  const renderStage = renderOperation.cancel.isPending
    ? 'Отменяем рендер'
    : renderOperation.start.isPending
      ? 'Подготовка рендера'
      : renderOperation.job?.stage || 'Рендер';
  const environmentControl = (
    <SceneEnvironmentControl
      loading={headerLoading}
      quietStatus={headerLoading && Boolean(activity)}
      applying={false}
      error={sceneFailure}
      preparation={displayed}
      canRefresh={sceneReady || Boolean(sceneFailure)}
      onRefresh={() => {
        if (scene.renderError) scene.retry();
        else environment.refresh();
      }}
      operationLabel={
        renderOperation.busy && !renderOpen && !sceneLoading
          ? renderOperation.start.isPending
            ? 'Подготовка рендера'
            : renderOperation.job?.stage || 'Рендер'
          : scene.modelsReady && loading
            ? 'Обновляем прогноз'
            : scene.modelsReady
              ? 'Обновляем 3D'
              : undefined
      }
      onData={() => setDataOpen(true)}
      onLocation={
        snapshot?.project_id ? () => setLocationOpen(true) : undefined
      }
    />
  );
  const headerControl =
    hasRender &&
    !renderOpen &&
    !sceneLoading &&
    (!active ||
      renderOperation.busy ||
      renderOperation.error ||
      renderOperation.job?.status === 'failed') ? (
      <IconButton
        icon={
          renderOperation.error || renderOperation.job?.status === 'failed' ? (
            <CircleAlert className="text-error" />
          ) : (
            <Camera
              className={
                headerLoading ? 'motion-safe:animate-pulse' : undefined
              }
            />
          )
        }
        variant="ghost"
        controlSize="compact"
        label={
          renderOperation.error || renderOperation.job?.status === 'failed'
            ? 'Рендер: ошибка. Открыть состояние'
            : renderOperation.busy
              ? `Рендер: ${renderStage}`
              : 'Открыть рендер'
        }
        data-busy={headerLoading || undefined}
        onClick={() => setRenderOpen(true)}
      />
    ) : (
      environmentControl
    );
  const hoveredObject = scene.hover
    ? snapshot?.objects.find(
        (object) => object.object_id === scene.hover?.objectId,
      )
    : undefined;
  const hoveredIssue = hoveredObject
    ? options.issues?.find(
        (issue) => issue.object_id === hoveredObject.object_id,
      )
    : undefined;
  return (
    <ControlProvider size="compact">
      {activity &&
        renderOperation.busy &&
        !renderOpen &&
        !sceneLoading &&
        !renderOperation.error && (
          <ViewportLoading
            label={renderStage}
            startedAt={renderOperation.start.submittedAt}
            {...activity}
          />
        )}
      <section
        className="hidden:hidden @container/canvas absolute inset-0 z-30 block min-w-0 bg-neutral-100 text-neutral-800"
        aria-label="Параметрический 3D-предпросмотр"
        hidden={!active}
        data-scene-preparation-id={appliedPreparation ?? ''}
        data-scene-preparation-sha256={displayed?.sha256 ?? ''}
        data-scene-preparation-ready={Boolean(displayed)}
        data-scene-environment-trees={
          displayed?.preview.context_trees.length ?? 0
        }
        data-scene-environment-meshes={displayed?.preview.meshes.length ?? 0}
        onKeyDown={(event) => {
          if (
            event.key === 'Enter' &&
            streetPicking &&
            !(
              event.target instanceof HTMLElement &&
              event.target.closest('button,input,select,textarea')
            )
          ) {
            event.preventDefault();
            scene.controllerRef.current?.confirmStreetPlacement();
          }
          if (event.key === 'Escape' && streetPicking) {
            scene.controllerRef.current?.cancelStreetPlacement();
            setStreetPlacement(undefined);
          }
        }}
        data-renderer-lifecycle={SCENE_RENDERER_LIFECYCLE_KEY}
        {...sceneTelemetryAttributes(scene.telemetry)}
      >
        {sceneLoading && activity && (
          <ViewportLoading
            label={
              !snapshot || loading
                ? firstFrameReady
                  ? 'Обновляем 3D'
                  : 'Подготовка сцены'
                : !scene.modelsReady
                  ? 'Запуск 3D'
                  : drawingFrame
                    ? firstFrameReady
                      ? 'Обновление кадра'
                      : 'Первый кадр'
                    : environmentApplying ||
                        (displayEnvironment &&
                          appliedPreparation !== displayEnvironment.id)
                      ? 'Загрузка моделей'
                      : 'Первый кадр'
            }
            {...activity}
          />
        )}
        {scene.telemetry && (
          <output className="sr-only" aria-hidden="true">
            {`${scene.telemetry.fps} FPS, ${scene.telemetry.drawCalls} draw calls, ${scene.telemetry.triangles} triangles, render scale ${scene.telemetry.renderScale}, DPR ${scene.telemetry.effectivePixelRatio}`}
          </output>
        )}
        <SceneCameraControls
          onFitZones={
            onFitZones
              ? () => {
                  scene.controllerRef.current?.cancelStreetPlacement();
                  setStreetPlacement(undefined);
                  setStreetView(false);
                  onFitZones();
                }
              : undefined
          }
          zoneCount={zoneCount}
          disabled={!scene.modelsReady || Boolean(scene.renderError)}
          streetDisabled={!sceneReady}
          hasSelection={selectedIds.length > 0}
          onZoom={(factor) => scene.controllerRef.current?.zoom(factor)}
          onOverview={() => {
            scene.controllerRef.current?.cancelStreetPlacement();
            setStreetPlacement(undefined);
            setStreetView(false);
            scene.controllerRef.current?.setCameraMode('overview');
          }}
          onFitSelection={() => {
            scene.controllerRef.current?.cancelStreetPlacement();
            setStreetPlacement(undefined);
            setStreetView(false);
            scene.controllerRef.current?.fitSelection();
          }}
          streetPicking={streetPicking}
          streetView={streetView}
          onLevelHorizon={() =>
            scene.controllerRef.current?.levelStreetHorizon()
          }
          onStreet={() => {
            if (streetPicking) {
              scene.controllerRef.current?.cancelStreetPlacement();
              setStreetPlacement(undefined);
              return;
            }
            scene.controllerRef.current?.beginStreetPlacement(() => {
              setStreetPlacement(undefined);
              setStreetView(true);
              environment.refresh();
            }, setStreetPlacement);
          }}
        />
        <SceneForecastControl
          visible={showGrowthControl}
          horizon={horizon}
          onHorizon={onHorizon}
        />
        <CanvasControlSlot position="topRight">
          <CanvasControlGroup
            label="Сцена и изображение"
            className="cursor-default"
          >
            {activity?.headerSlot
              ? (active || hasRender) &&
                createPortal(headerControl, activity.headerSlot)
              : !activity
                ? environmentControl
                : null}
            {snapshot?.project_id && (
              <SceneRenderControl
                open={renderOpen}
                onOpenChange={setRenderOpen}
                backgroundIndicator={!activity}
                className="static"
                variant="ghost"
                key={snapshot.project_id}
                snapshot={snapshot}
                operation={renderOperation}
                ready={
                  active &&
                  scene.modelsReady &&
                  firstFrameReady &&
                  !environmentApplying &&
                  !drawingFrame &&
                  snapshot.horizon_year === horizon &&
                  !loading &&
                  !error &&
                  !scene.renderError &&
                  !environment.loading &&
                  Boolean(displayed) &&
                  !streetPicking &&
                  (!environment.preparation ||
                    appliedPreparation === environment.preparation.id ||
                    Boolean(environmentError))
                }
                preparation={displayed}
                getCamera={() =>
                  scene.controllerRef.current?.exportRenderCamera()
                }
              />
            )}
            {viewControls && (
              <div className="flex shrink-0 items-center gap-1">
                <span className="hidden @min-[440px]/canvas:contents">
                  <Divider orientation="vertical" />
                </span>
                {viewControls}
              </div>
            )}
          </CanvasControlGroup>
          <div className="pointer-events-auto grid w-full max-w-96 justify-items-end gap-2">
            {(!activity || (!firstFrameReady && sceneFailure)) && (
              <SceneStatus
                horizon={horizon}
                snapshotHorizon={snapshot?.horizon_year}
                loading={loading}
                error={error || currentEnvironmentError}
                modelsReady={scene.modelsReady}
                renderError={scene.renderError}
                onRetry={scene.retry}
                onRetryScene={options.onRetryScene}
              />
            )}
            {streetPlacement && sceneReady ? (
              <SceneStreetPlacementControl
                state={streetPlacement}
                onConfirm={() =>
                  scene.controllerRef.current?.confirmStreetPlacement()
                }
                onRestart={() =>
                  scene.controllerRef.current?.resetStreetPlacement()
                }
                onCancel={() => {
                  scene.controllerRef.current?.cancelStreetPlacement();
                  setStreetPlacement(undefined);
                }}
              />
            ) : null}
          </div>
        </CanvasControlSlot>
        <SceneDataDialog
          open={dataOpen}
          snapshot={snapshot}
          zoneCount={options.zones?.length ?? 0}
          showRoots={scene.showRoots}
          onRootsChange={scene.setShowRoots}
          onClose={() => setDataOpen(false)}
        />
        {snapshot?.project_id && locationOpen && (
          <ProjectLocationDialog
            projectId={snapshot.project_id}
            open={locationOpen}
            onClose={() => setLocationOpen(false)}
            onApplied={() => environment.refresh()}
          />
        )}
        <div className="absolute inset-0 min-h-0 overflow-hidden bg-neutral-100">
          <canvas
            ref={scene.canvasRef}
            tabIndex={0}
            className={`block size-full touch-none ${streetPicking ? 'cursor-crosshair' : 'cursor-grab active:cursor-grabbing'}`}
            aria-label={SCENE_ACCESSIBILITY_CONTRACT.canvasLabel}
          />
          {scene.hover && hoveredObject && (
            <SceneHoverCard
              position={scene.hover}
              object={hoveredObject}
              issue={hoveredIssue}
            />
          )}
        </div>
      </section>
    </ControlProvider>
  );
};
