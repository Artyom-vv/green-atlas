import { MapFeedback } from '@/widgets/map/ui/MapFeedback';
import { Progress } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasActivityProps extends Pick<
  WorkspaceReadyModel,
  | 'savingPlan'
  | 'mapGeometryMetadata'
  | 'mapGeometryQuery'
  | 'cadBaseReady'
  | 'moveLiveCheck'
  | 'placementCheck'
  | 'previewChanges'
  | 'previewPattern'
  | 'previewRecommendation'
  | 'showMapStatus'
  | 'tool'
> {}
export const CanvasActivityPropsFor = (props: CanvasActivityProps) => ({
  savingPlan: props.savingPlan,
  mapGeometryMetadata: props.mapGeometryMetadata,
  mapGeometryQuery: props.mapGeometryQuery,
  cadBaseReady: props.cadBaseReady,
  moveLiveCheck: props.moveLiveCheck,
  placementCheck: props.placementCheck,
  previewChanges: props.previewChanges,
  previewPattern: props.previewPattern,
  previewRecommendation: props.previewRecommendation,
  showMapStatus: props.showMapStatus,
  tool: props.tool,
});
export const CanvasActivity: FC<CanvasActivityProps> = ({
  savingPlan,
  mapGeometryMetadata,
  mapGeometryQuery,
  cadBaseReady,
  moveLiveCheck,
  placementCheck,
  previewChanges,
  previewPattern,
  previewRecommendation,
  showMapStatus,
  tool,
}) => (
  <>
    {showMapStatus && (
      <div className="pointer-events-none absolute top-33 left-18 z-40 flex max-w-[calc(100%-10rem)] flex-col items-start gap-1">
        {placementCheck && (tool === 'add_tree' || tool === 'add_shrub') && (
          <MapFeedback status={placementCheck.status}>
            {placementCheck.reason}
          </MapFeedback>
        )}
        {moveLiveCheck && (tool === 'move' || tool === 'select') && (
          <MapFeedback status={moveLiveCheck.status}>
            {moveLiveCheck.reason}
          </MapFeedback>
        )}
        {mapGeometryQuery.isFetching && (
          <span className="rounded-control bg-white px-3 py-2 text-xs text-neutral-600">
            {cadBaseReady ? 'Обновляем объекты для выбора' : 'Обновляем карту'}
          </span>
        )}
        {mapGeometryMetadata?.truncated && (
          <span
            className="rounded-control text-warning-strong bg-white px-3 py-2 text-xs font-medium"
            role="status"
          >
            {cadBaseReady
              ? 'Приблизьте карту, чтобы выбрать отдельные объекты'
              : 'Приблизьте карту, чтобы увидеть детали'}
          </span>
        )}
      </div>
    )}
    {(savingPlan ||
      previewChanges.isPending ||
      previewPattern.isPending ||
      previewRecommendation.isPending) && (
      <div className="rounded-card absolute top-16 left-1/2 z-40 w-65 max-w-[calc(100%-2rem)] -translate-x-1/2 border border-solid border-neutral-200 bg-white p-3">
        <Progress
          label={
            previewChanges.isPending ||
            previewPattern.isPending ||
            previewRecommendation.isPending
              ? 'Проверяем размещение'
              : 'Сохраняем изменения'
          }
        />
      </div>
    )}
  </>
);
