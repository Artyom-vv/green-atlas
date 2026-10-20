import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import {
  CanvasGrowthScenario,
  CanvasGrowthScenarioPropsFor,
  type CanvasGrowthScenarioProps,
} from './canvas/CanvasGrowthScenario';
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
    CanvasToolsProps,
    CanvasViewControlsProps,
    CanvasGrowthScenarioProps,
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
  ...CanvasToolsPropsFor(model),
  ...CanvasViewControlsPropsFor(model),
  ...CanvasGrowthScenarioPropsFor(model),
  ...PlanViewportPropsFor(model),
  ...WorkspaceScenePropsFor(model),
});

export const WorkspaceCanvas: FC<WorkspaceCanvasProps> = (props) => (
  <section
    className="@container/canvas relative size-full min-h-0 min-w-0 overflow-hidden bg-neutral-100"
    onMouseLeave={() => props.setMapHoverTarget(undefined)}
  >
    <PlanViewport {...PlanViewportPropsFor(props)} />
    {!props.planComparison.active && (
      <>
        <CanvasScope {...CanvasScopePropsFor(props)} />
        <CanvasInspection {...CanvasInspectionPropsFor(props)} />
        <CanvasDraftActions {...CanvasDraftActionsPropsFor(props)} />
        <CanvasTools {...CanvasToolsPropsFor(props)} />
        <CanvasPreviewActions {...CanvasPreviewActionsPropsFor(props)} />
      </>
    )}
    <CanvasNavigation {...CanvasNavigationPropsFor(props)} />
    <CanvasActivity {...CanvasActivityPropsFor(props)} />
    <WorkspaceScene
      {...WorkspaceScenePropsFor(props)}
      viewControls={
        <CanvasViewControls {...CanvasViewControlsPropsFor(props)} embedded />
      }
    />
    {!props.sceneOpen && (
      <CanvasViewControls {...CanvasViewControlsPropsFor(props)} />
    )}
    <CanvasRecovery {...CanvasRecoveryPropsFor(props)} />
    <CanvasGrowthScenario {...CanvasGrowthScenarioPropsFor(props)} />
  </section>
);
