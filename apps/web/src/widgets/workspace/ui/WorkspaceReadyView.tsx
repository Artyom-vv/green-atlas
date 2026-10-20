import type { FC, ReactNode } from 'react';
import { SceneActivityProvider } from '@/widgets/scene/model/SceneActivityContext';
import { WorkspacePlantCatalog } from './WorkspacePlantCatalog';
import { IDEWorkspaceShell } from '@/widgets/workbench';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import { WorkspaceCanvas, WorkspaceCanvasPropsFor } from './WorkspaceCanvas';
import { WorkspaceHeader, WorkspaceHeaderPropsFor } from './WorkspaceHeader';
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
  WorkspaceDialogs,
  WorkspaceDialogsPropsFor,
} from './dialogs/WorkspaceDialogs';
import { workspaceResults, workspaceResultsPropsFor } from './workspaceResults';
import { WorkspaceGeometryNotice } from './dialogs/WorkspaceGeometryNotice';

interface WorkspaceReadyViewProps {
  model: WorkspaceReadyModel;
}

export const WorkspaceReadyView: FC<WorkspaceReadyViewProps> = ({ model }) => {
  const editingContent = (content: ReactNode) => (
    <div className="contents" inert={model.planComparison.active}>
      {content}
    </div>
  );
  const patternTool = (
    <WorkspacePatternTool {...WorkspacePatternToolPropsFor(model)} />
  );
  const results = workspaceResults(workspaceResultsPropsFor(model));
  return (
    <SceneActivityProvider>
      <WorkspacePlantCatalog>
        <IDEWorkspaceShell
          header={<WorkspaceHeader {...WorkspaceHeaderPropsFor(model)} />}
          map={<WorkspaceCanvas {...WorkspaceCanvasPropsFor(model)} />}
          resources={editingContent(
            <WorkspaceResources {...WorkspaceResourcesPropsFor(model)} />,
          )}
          inspector={editingContent(
            <WorkspaceInspector {...WorkspaceInspectorPropsFor(model)} />,
          )}
          tool={editingContent(
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
            />,
          )}
          resourcesOpen={model.leftOpen}
          onResourcesOpenChange={model.setLeftOpen}
          results={{
            checks: editingContent(results.checks),
            schedule: editingContent(results.schedule),
            history: editingContent(results.history),
          }}
          resultsActions={results.actions}
          rightTab={model.ideRightTab}
          onRightTabChange={model.changeIdeRightTab}
          rightOpen={model.rightOpen}
          onRightOpenChange={model.setRightOpen}
          resultsOpen={model.resultsOpen}
          onResultsOpenChange={model.setResultsOpen}
          resultsTab={
            model.resultsTab === 'issues' ? 'checks' : model.resultsTab
          }
          onResultsTabChange={(tab) =>
            model.setResultsTab(tab === 'checks' ? 'issues' : tab)
          }
        />
        <WorkspaceDialogs {...WorkspaceDialogsPropsFor(model)} />
        <WorkspaceGeometryNotice
          key={model.projectId}
          projectId={model.projectId}
          prepared={Boolean(
            model.project.map_ready &&
            !model.project.source_review &&
            !model.preparationIssue,
          )}
        />
      </WorkspacePlantCatalog>
    </SceneActivityProvider>
  );
};
