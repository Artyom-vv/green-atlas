import { ControlProvider, IconButton } from '@green/ui';
import { Info } from 'lucide-react';
import { useState, type FC, type Ref } from 'react';
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

export type { SceneReviewHandle } from '@/widgets/scene/model/sceneContracts';

export interface SceneReviewProps extends SceneReviewOptions {
  ref?: Ref<SceneReviewHandle>;
}

export const SceneReview: FC<SceneReviewProps> = ({ ref, ...options }) => {
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
  const scene = useSceneRenderer(options, ref);
  const [dataOpen, setDataOpen] = useState(false);
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
      <section
        className="hidden:hidden @container/scene absolute inset-0 z-30 block min-w-0 bg-neutral-100 text-neutral-800"
        aria-label="Параметрический 3D-предпросмотр"
        hidden={!active}
        data-renderer-lifecycle={SCENE_RENDERER_LIFECYCLE_KEY}
        {...sceneTelemetryAttributes(scene.telemetry)}
      >
        {scene.telemetry && (
          <output className="sr-only" aria-hidden="true">
            {`${scene.telemetry.fps} FPS, ${scene.telemetry.drawCalls} draw calls, ${scene.telemetry.triangles} triangles, render scale ${scene.telemetry.renderScale}, DPR ${scene.telemetry.effectivePixelRatio}`}
          </output>
        )}
        <SceneCameraControls
          hasSelection={selectedIds.length > 0}
          onZoom={(factor) => scene.controllerRef.current?.zoom(factor)}
          onOverview={() =>
            scene.controllerRef.current?.setCameraMode('overview')
          }
          onFitSelection={() => scene.controllerRef.current?.fitSelection()}
        />
        <SceneForecastControl
          visible={showGrowthControl}
          horizon={horizon}
          onHorizon={onHorizon}
        />
        <IconButton
          className="absolute bottom-2 left-4 z-4 bg-white"
          icon={Info}
          label="Данные и управление 3D"
          variant="secondary"
          onClick={() => setDataOpen(true)}
        />
        <SceneDataDialog
          open={dataOpen}
          snapshot={snapshot}
          zoneCount={options.zones?.length ?? 0}
          showRoots={scene.showRoots}
          onRootsChange={scene.setShowRoots}
          onClose={() => setDataOpen(false)}
        />
        <div className="absolute inset-0 min-h-0 overflow-hidden bg-neutral-100">
          <canvas
            ref={scene.canvasRef}
            className="block size-full cursor-grab touch-none active:cursor-grabbing"
            aria-label={SCENE_ACCESSIBILITY_CONTRACT.canvasLabel}
          />
          <SceneStatus
            horizon={horizon}
            snapshotHorizon={snapshot?.horizon_year}
            loading={loading}
            error={error}
            modelsReady={scene.modelsReady}
            renderError={scene.renderError}
            onRetry={scene.retry}
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
