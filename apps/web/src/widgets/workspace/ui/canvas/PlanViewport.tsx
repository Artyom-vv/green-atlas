import { MapViewport } from '@/widgets/map/ui/MapViewport';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';
import {
  viewportDrawingBindings,
  viewportDrawingPropsFor,
  type ViewportDrawingProps,
} from '../../model/viewport/viewportDrawing';
import {
  viewportRowBindings,
  viewportRowPropsFor,
  type ViewportRowProps,
} from '../../model/viewport/viewportRow';
import {
  viewportBrushBindings,
  viewportBrushPropsFor,
  type ViewportBrushProps,
} from '../../model/viewport/viewportBrush';
import {
  viewportSelectionBindings,
  viewportSelectionPropsFor,
  type ViewportSelectionProps,
} from '../../model/viewport/viewportSelection';
import {
  viewportPresentationBindings,
  viewportPresentationPropsFor,
  type ViewportPresentationProps,
} from '../../model/viewport/viewportPresentation';

export interface PlanViewportProps
  extends
    Pick<WorkspaceReadyModel, 'projectId' | 'mapViewport' | 'sceneOpen'>,
    ViewportDrawingProps,
    ViewportRowProps,
    ViewportBrushProps,
    ViewportSelectionProps,
    ViewportPresentationProps {}
export const PlanViewportPropsFor = (
  props: PlanViewportProps,
): PlanViewportProps => ({
  projectId: props.projectId,
  mapViewport: props.mapViewport,
  sceneOpen: props.sceneOpen,
  ...viewportDrawingPropsFor(props),
  ...viewportRowPropsFor(props),
  ...viewportBrushPropsFor(props),
  ...viewportSelectionPropsFor(props),
  ...viewportPresentationPropsFor(props),
});
export const PlanViewport: FC<PlanViewportProps> = (props) => (
  <div
    className="absolute inset-0"
    inert={props.sceneOpen}
    aria-hidden={props.sceneOpen}
  >
    <MapViewport
      key={props.projectId}
      ref={props.mapViewport}
      {...viewportDrawingBindings(props)}
      {...viewportRowBindings(props)}
      {...viewportBrushBindings(props)}
      {...viewportSelectionBindings(props)}
      {...viewportPresentationBindings(props)}
    />
  </div>
);
