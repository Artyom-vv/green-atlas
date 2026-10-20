import { MapFeedback } from '@/widgets/map/ui/MapFeedback';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';
import { ViewportLoading } from '@/shared/ui/ViewportLoading';
import { useSceneActivity } from '@/widgets/scene/model/SceneActivityContext';

export interface CanvasActivityProps extends Pick<
  WorkspaceReadyModel,
  | 'mapGeometryMetadata'
  | 'cadBaseReady'
  | 'mapLoading'
  | 'mapLoadingLabel'
  | 'sceneOpen'
  | 'mapGeometryDelivery'
  | 'moveLiveCheck'
  | 'placementCheck'
  | 'planComparison'
  | 'showMapStatus'
  | 'tool'
> {}
export const CanvasActivityPropsFor = (props: CanvasActivityProps) => ({
  mapGeometryMetadata: props.mapGeometryMetadata,
  cadBaseReady: props.cadBaseReady,
  mapLoading: props.mapLoading,
  mapLoadingLabel: props.mapLoadingLabel,
  sceneOpen: props.sceneOpen,
  mapGeometryDelivery: props.mapGeometryDelivery,
  moveLiveCheck: props.moveLiveCheck,
  placementCheck: props.placementCheck,
  planComparison: props.planComparison,
  showMapStatus: props.showMapStatus,
  tool: props.tool,
});
export const CanvasActivity: FC<CanvasActivityProps> = ({
  mapGeometryMetadata,
  cadBaseReady,
  mapLoading,
  mapLoadingLabel,
  sceneOpen,
  moveLiveCheck,
  placementCheck,
  planComparison,
  showMapStatus,
  tool,
}) => {
  const activity = useSceneActivity();
  return (
    <>
      {!sceneOpen && mapLoading && (
        <ViewportLoading
          label={mapLoadingLabel}
          {...activity}
        />
      )}
      {!planComparison.active &&
        showMapStatus &&
        (placementCheck || moveLiveCheck) && (
          <div className="pointer-events-none absolute top-33 left-18 z-40 flex max-w-[calc(100%-10rem)] flex-col items-start gap-1">
            {placementCheck &&
              (tool === 'add_tree' || tool === 'add_shrub') && (
                <MapFeedback status={placementCheck.status}>
                  {placementCheck.reason}
                </MapFeedback>
              )}
            {moveLiveCheck && (tool === 'move' || tool === 'select') && (
              <MapFeedback status={moveLiveCheck.status}>
                {moveLiveCheck.reason}
              </MapFeedback>
            )}
          </div>
        )}
      {showMapStatus && mapGeometryMetadata?.truncated && (
        <div
          className="rounded-control bottom-canvas-inset pointer-events-none absolute right-[calc(var(--spacing-canvas-inset)+var(--spacing-canvas-rail)+var(--spacing-canvas-gap))] z-30 max-w-[calc(100%-var(--spacing-canvas-inset)*2-var(--spacing-canvas-rail)-var(--spacing-canvas-gap))] bg-white/90 px-2 py-1 text-xs text-neutral-600"
          role="status"
          aria-live="polite"
        >
          {cadBaseReady
            ? 'Приблизьте для выбора отдельных объектов'
            : 'Приблизьте карту для деталей'}
        </div>
      )}
    </>
  );
};
