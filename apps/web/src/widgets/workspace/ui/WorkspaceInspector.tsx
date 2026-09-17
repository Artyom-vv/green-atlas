import {
  workspaceNewZonesPropsFor,
  type WorkspaceNewZonesProps,
} from './WorkspaceNewZones.props';
import {
  workspaceSourceInspectorPropsFor,
  type WorkspaceSourceInspectorProps,
} from './WorkspaceSourceInspector.props';
import {
  workspaceSelectionInspectorPropsFor,
  type WorkspaceSelectionInspectorProps,
} from './WorkspaceSelectionInspector.props';
import type { FC } from 'react';
import { Button, InlineMessage } from '@green/ui';
import { LayerInspector } from '@/entities/source-data/ui/LayerInspector';
import { PlantingsOverviewPanel } from '@/widgets/selection-inspector/ui/PlantingsOverviewPanel';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import { WorkspaceNewZones } from './WorkspaceNewZones';
import { WorkspaceSourceInspector } from './WorkspaceSourceInspector';
import { WorkspaceSelectionInspector } from './WorkspaceSelectionInspector';
export interface WorkspaceInspectorProps
  extends
    WorkspaceNewZonesProps,
    WorkspaceSourceInspectorProps,
    WorkspaceSelectionInspectorProps,
    Pick<
      WorkspaceReadyModel,
      'activeLayer' | 'planObjects' | 'setVisibility' | 'visibility'
    > {}
export const WorkspaceInspectorPropsFor = (
  model: WorkspaceInspectorProps,
): WorkspaceInspectorProps => ({
  ...workspaceNewZonesPropsFor(model),
  ...workspaceSourceInspectorPropsFor(model),
  ...workspaceSelectionInspectorPropsFor(model),
  activeLayer: model.activeLayer,
  planObjects: model.planObjects,
  setVisibility: model.setVisibility,
  visibility: model.visibility,
});
export const WorkspaceInspector: FC<WorkspaceInspectorProps> = (props) => {
  const {
    activateTool,
    activeLayer,
    changeMapMode,
    createManualPlan,
    inspectorView,
    mapViewport,
    planObjects,
    project,
    sceneOpen,
    sceneReview,
    setVisibility,
    visibility,
  } = props;
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {createManualPlan.notice && (
          <InlineMessage tone="info">{createManualPlan.notice}</InlineMessage>
        )}
        {createManualPlan.recovery && (
          <Button
            variant="secondary"
            onClick={createManualPlan.recovery.onRetry}
          >
            Проверить состояние плана
          </Button>
        )}
        <WorkspaceNewZones {...props} />

        {inspectorView === 'overview' && project.plan && (
          <PlantingsOverviewPanel
            mapMode={sceneOpen ? '3d' : '2d'}
            objects={planObjects}
            onPlace={() => {
              if (sceneOpen) changeMapMode('2d');
              activateTool('pattern_fill');
            }}
            onFit={() =>
              sceneOpen
                ? sceneReview.current?.fitPlantings()
                : mapViewport.current?.fitPlan()
            }
          />
        )}
        {inspectorView === 'layer' && activeLayer && (
          <LayerInspector
            layer={activeLayer}
            visible={visibility[activeLayer.id] !== false}
            onVisibility={(visible) =>
              setVisibility((current) => ({
                ...current,
                [activeLayer.id]: visible,
              }))
            }
            onFit={() =>
              mapViewport.current?.fitLayer(
                activeLayer.source_name,
                activeLayer.bounds,
              )
            }
          />
        )}

        <WorkspaceSourceInspector {...props} />

        <WorkspaceSelectionInspector {...props} />
      </div>
    </div>
  );
};
