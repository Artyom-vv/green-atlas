import { MapToolbar } from '@/widgets/map/ui/MapToolbar';
import { CanvasControlSlot } from '@/shared/ui/CanvasControls';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasToolsProps extends Pick<
  WorkspaceReadyModel,
  | 'activateTool'
  | 'changeMapMode'
  | 'editorBusy'
  | 'hasUnsavedWork'
  | 'mapPanActive'
  | 'planLocked'
  | 'projectHasPlan'
  | 'sceneOpen'
  | 'selectedIds'
  | 'selectedLocked'
  | 'setDeleteSelectionOpen'
  | 'setMapPanActive'
  | 'sourcePreview'
  | 'tool'
> {}
export const CanvasToolsPropsFor = (props: CanvasToolsProps) => ({
  activateTool: props.activateTool,
  changeMapMode: props.changeMapMode,
  editorBusy: props.editorBusy,
  hasUnsavedWork: props.hasUnsavedWork,
  mapPanActive: props.mapPanActive,
  planLocked: props.planLocked,
  projectHasPlan: props.projectHasPlan,
  sceneOpen: props.sceneOpen,
  selectedIds: props.selectedIds,
  selectedLocked: props.selectedLocked,
  setDeleteSelectionOpen: props.setDeleteSelectionOpen,
  setMapPanActive: props.setMapPanActive,
  sourcePreview: props.sourcePreview,
  tool: props.tool,
});
export const CanvasTools: FC<CanvasToolsProps> = ({
  activateTool,
  changeMapMode,
  editorBusy,
  hasUnsavedWork,
  mapPanActive,
  planLocked,
  projectHasPlan,
  sceneOpen,
  selectedIds,
  selectedLocked,
  setDeleteSelectionOpen,
  setMapPanActive,
  sourcePreview,
  tool,
}) => (
  <>
    {projectHasPlan && (
      <CanvasControlSlot position="topLeft">
        <MapToolbar
          mapMode={sceneOpen ? '3d' : '2d'}
          tool={mapPanActive ? 'pan' : sceneOpen ? 'select' : tool}
          onTool={(next) => {
            if (next === 'pan') {
              setMapPanActive(!mapPanActive);
              return;
            }
            if (mapPanActive && next === tool) {
              setMapPanActive(false);
              return;
            }
            setMapPanActive(false);
            if (sceneOpen && next === 'select') {
              setMapPanActive(false);
              return;
            }
            if (sceneOpen) changeMapMode('2d');
            activateTool(next);
          }}
          editable={!sourcePreview && !planLocked && !editorBusy}
          canDelete={
            selectedIds.length > 0 && !selectedLocked && !hasUnsavedWork
          }
          onDelete={() => setDeleteSelectionOpen(true)}
        />
      </CanvasControlSlot>
    )}
  </>
);
