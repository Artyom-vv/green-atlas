import type { FC } from 'react';
import { featureAvailability } from '@/shared/config/featureAvailability';
import { IDEWorkspaceShell } from '@/widgets/workbench';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import { WorkspaceCanvas, WorkspaceCanvasPropsFor } from './WorkspaceCanvas';
import { WorkspaceHeader, WorkspaceHeaderPropsFor } from './WorkspaceHeader';
import { SourceReviewNotice } from './SourceReviewNotice';
import {
  WorkspaceResources,
  WorkspaceResourcesPropsFor,
} from './WorkspaceResources';
import {
  WorkspaceInspector,
  WorkspaceInspectorPropsFor,
} from './WorkspaceInspector';
import {
  WorkspacePatternTool,
  WorkspacePatternToolPropsFor,
} from './WorkspacePatternTool';
import {
  WorkspaceBrushTool,
  WorkspaceBrushToolPropsFor,
} from './WorkspaceBrushTool';
import {
  WorkspacePlacementTool,
  WorkspacePlacementToolPropsFor,
} from './WorkspacePlacementTool';
import {
  WorkspaceToolSlot,
  WorkspaceToolSlotPropsFor,
} from './WorkspaceToolSlot';
import {
  WorkspaceAssistantSlot,
  WorkspaceAssistantSlotPropsFor,
} from './WorkspaceAssistantSlot';
import {
  WorkspaceDialogs,
  WorkspaceDialogsPropsFor,
} from './dialogs/WorkspaceDialogs';
import { workspaceResults, workspaceResultsPropsFor } from './workspaceResults';

interface WorkspaceReadyViewProps {
  model: WorkspaceReadyModel;
}

export const WorkspaceReadyView: FC<WorkspaceReadyViewProps> = ({ model }) => {
  const patternTool = (
    <WorkspacePatternTool {...WorkspacePatternToolPropsFor(model)} />
  );
  return (
    <>
      <IDEWorkspaceShell
        header={
          <div>
            <WorkspaceHeader {...WorkspaceHeaderPropsFor(model)} />
            {model.project.source_review && (
              <SourceReviewNotice
                incompleteGeometry={model.project.source_review.issues?.some(
                  (issue) => issue.code === 'incomplete_layer',
                )}
                onReview={() =>
                  model.leaveWorkspace(`/projects/${model.project.id}/setup`)
                }
              />
            )}
          </div>
        }
        map={<WorkspaceCanvas {...WorkspaceCanvasPropsFor(model)} />}
        resources={
          <WorkspaceResources {...WorkspaceResourcesPropsFor(model)} />
        }
        inspector={
          <WorkspaceInspector {...WorkspaceInspectorPropsFor(model)} />
        }
        tool={
          <WorkspaceToolSlot
            {...WorkspaceToolSlotPropsFor(model)}
            pattern={patternTool}
            brush={
              <WorkspaceBrushTool {...WorkspaceBrushToolPropsFor(model)} />
            }
            placement={
              <WorkspacePlacementTool
                {...WorkspacePlacementToolPropsFor(model)}
                patternTool={patternTool}
              />
            }
          />
        }
        assistant={
          featureAvailability.assistant && (
            <WorkspaceAssistantSlot
              {...WorkspaceAssistantSlotPropsFor(model)}
            />
          )
        }
        resourcesOpen={model.leftOpen}
        onResourcesOpenChange={model.setLeftOpen}
        results={workspaceResults(workspaceResultsPropsFor(model))}
        rightTab={model.ideRightTab}
        onRightTabChange={model.changeIdeRightTab}
        rightOpen={model.rightOpen}
        onRightOpenChange={model.setRightOpen}
        resultsOpen={model.resultsOpen}
        onResultsOpenChange={model.setResultsOpen}
        resultsTab={model.resultsTab === 'issues' ? 'checks' : model.resultsTab}
        onResultsTabChange={(tab) =>
          model.setResultsTab(tab === 'checks' ? 'issues' : tab)
        }
      />
      <WorkspaceDialogs {...WorkspaceDialogsPropsFor(model)} />
    </>
  );
};
