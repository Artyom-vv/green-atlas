import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import {
  CanvasActivity,
  CanvasActivityPropsFor,
  type CanvasActivityProps,
} from './canvas/CanvasActivity';
import {
  CanvasDraftActions,
  CanvasDraftActionsPropsFor,
  type CanvasDraftActionsProps,
} from './canvas/CanvasDraftActions';
import {
  CanvasInspection,
  CanvasInspectionPropsFor,
  type CanvasInspectionProps,
} from './canvas/CanvasInspection';
import {
  CanvasNavigation,
  CanvasNavigationPropsFor,
  type CanvasNavigationProps,
} from './canvas/CanvasNavigation';
import {
  CanvasPreviewActions,
  CanvasPreviewActionsPropsFor,
  type CanvasPreviewActionsProps,
} from './canvas/CanvasPreviewActions';
import {
  CanvasRecovery,
  CanvasRecoveryPropsFor,
  type CanvasRecoveryProps,
} from './canvas/CanvasRecovery';
import {
  CanvasScope,
  CanvasScopePropsFor,
  type CanvasScopeProps,
} from './canvas/CanvasScope';
import {
  CanvasSelectionActions,
  CanvasSelectionActionsPropsFor,
  type CanvasSelectionActionsProps,
} from './canvas/CanvasSelectionActions';
import {
  CanvasTools,
  CanvasToolsPropsFor,
  type CanvasToolsProps,
} from './canvas/CanvasTools';
import {
  CanvasViewControls,
  CanvasViewControlsPropsFor,
  type CanvasViewControlsProps,
} from './canvas/CanvasViewControls';
import {
  PlanViewport,
  PlanViewportPropsFor,
  type PlanViewportProps,
} from './canvas/PlanViewport';
import {
  WorkspaceScene,
  WorkspaceScenePropsFor,
  type WorkspaceSceneProps,
} from './canvas/WorkspaceScene';

export interface WorkspaceCanvasProps
  extends
    CanvasActivityProps,
    CanvasDraftActionsProps,
    CanvasInspectionProps,
    CanvasNavigationProps,
    CanvasPreviewActionsProps,
    CanvasRecoveryProps,
    CanvasScopeProps,
    CanvasSelectionActionsProps,
    CanvasToolsProps,
    CanvasViewControlsProps,
    PlanViewportProps,
    WorkspaceSceneProps {}

export const WorkspaceCanvasPropsFor = (
  model: WorkspaceReadyModel,
): WorkspaceCanvasProps => ({
  ...CanvasActivityPropsFor(model),
  ...CanvasDraftActionsPropsFor(model),
  ...CanvasInspectionPropsFor(model),
  ...CanvasNavigationPropsFor(model),
  ...CanvasPreviewActionsPropsFor(model),
  ...CanvasRecoveryPropsFor(model),
  ...CanvasScopePropsFor(model),
  ...CanvasSelectionActionsPropsFor(model),
  ...CanvasToolsPropsFor(model),
  ...CanvasViewControlsPropsFor(model),
  ...PlanViewportPropsFor(model),
  ...WorkspaceScenePropsFor(model),
});

export const WorkspaceCanvas: FC<WorkspaceCanvasProps> = (props) => (
  <section
    className="relative size-full min-h-0 min-w-0 overflow-hidden bg-neutral-100"
    onMouseLeave={() => props.setMapHoverTarget(undefined)}
  >
    <CanvasScope {...CanvasScopePropsFor(props)} />
    <PlanViewport {...PlanViewportPropsFor(props)} />
    <CanvasInspection {...CanvasInspectionPropsFor(props)} />
    <CanvasDraftActions {...CanvasDraftActionsPropsFor(props)} />
    <CanvasTools {...CanvasToolsPropsFor(props)} />
    <CanvasSelectionActions {...CanvasSelectionActionsPropsFor(props)} />
    <CanvasNavigation {...CanvasNavigationPropsFor(props)} />
    <CanvasActivity {...CanvasActivityPropsFor(props)} />
    <WorkspaceScene {...WorkspaceScenePropsFor(props)} />
    <CanvasViewControls {...CanvasViewControlsPropsFor(props)} />
    <CanvasPreviewActions {...CanvasPreviewActionsPropsFor(props)} />
    <CanvasRecovery {...CanvasRecoveryPropsFor(props)} />
  </section>
);
