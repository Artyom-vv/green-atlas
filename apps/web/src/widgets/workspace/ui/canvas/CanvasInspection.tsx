import { MapHoverCard } from '@/widgets/map/ui/MapHoverCard';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasInspectionProps extends Pick<
  WorkspaceReadyModel,
  | 'handleMapStackSelect'
  | 'mapInfoTarget'
  | 'mapInspectTarget'
  | 'sceneOpen'
  | 'selectedObjects'
  | 'setMapInspectTarget'
  | 'tool'
> {}
export const CanvasInspectionPropsFor = (props: CanvasInspectionProps) => ({
  handleMapStackSelect: props.handleMapStackSelect,
  mapInfoTarget: props.mapInfoTarget,
  mapInspectTarget: props.mapInspectTarget,
  sceneOpen: props.sceneOpen,
  selectedObjects: props.selectedObjects,
  setMapInspectTarget: props.setMapInspectTarget,
  tool: props.tool,
});
export const CanvasInspection: FC<CanvasInspectionProps> = ({
  handleMapStackSelect,
  mapInfoTarget,
  mapInspectTarget,
  sceneOpen,
  selectedObjects,
  setMapInspectTarget,
  tool,
}) => (
  <>
    {!sceneOpen &&
      (!selectedObjects.length || mapInfoTarget?.kind === 'preview') &&
      mapInfoTarget &&
      (tool === 'select' || tool === 'pattern_fill') && (
        <MapHoverCard
          target={mapInfoTarget}
          onSelect={
            mapInspectTarget
              ? (item) => {
                  handleMapStackSelect(item);
                  setMapInspectTarget(undefined);
                }
              : undefined
          }
        />
      )}
  </>
);
