import {
  workspaceRecommendationPropsFor,
  type WorkspaceRecommendationToolProps,
} from './WorkspaceRecommendationTool.props';
import { PlacementWorkspace } from '@/widgets/placement-workspace';
import type { FC, ReactNode } from 'react';
import { WorkspaceRecommendationTool } from './WorkspaceRecommendationTool';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

export interface WorkspacePlacementToolProps
  extends
    WorkspaceRecommendationToolProps,
    Pick<
      WorkspaceReadyModel,
      | 'busy'
      | 'closeRightPanel'
      | 'patternPreview'
      | 'patternSettingsOpen'
      | 'pendingZone'
      | 'placementAreaDrawing'
    > {
  patternTool: ReactNode;
}
export const WorkspacePlacementToolPropsFor = (
  model: Omit<WorkspacePlacementToolProps, 'patternTool'>,
): Omit<WorkspacePlacementToolProps, 'patternTool'> => ({
  ...workspaceRecommendationPropsFor(model),
  busy: model.busy,
  closeRightPanel: model.closeRightPanel,
  patternPreview: model.patternPreview,
  patternSettingsOpen: model.patternSettingsOpen,
  pendingZone: model.pendingZone,
  placementAreaDrawing: model.placementAreaDrawing,
});
export const WorkspacePlacementTool: FC<WorkspacePlacementToolProps> = (
  props,
) => {
  const {
    busy,
    closeRightPanel,
    patternPreview,
    patternSettingsOpen,
    patternTool,
    pendingZone,
    placementAreaDrawing,
    recommendationOpen,
    recommendationPreview,
    setRecommendationOpen,
  } = props;
  return (
    <PlacementWorkspace
      showCloseControl={false}
      busy={busy || placementAreaDrawing}
      open={
        patternSettingsOpen ||
        placementAreaDrawing ||
        pendingZone?.purpose === 'place'
      }
      hasPreview={Boolean(patternPreview || recommendationPreview)}
      recommendation={recommendationOpen}
      onRecommendation={setRecommendationOpen}
      onClose={closeRightPanel}
      manual={patternTool}
      automatic={<WorkspaceRecommendationTool {...props} />}
    />
  );
};
