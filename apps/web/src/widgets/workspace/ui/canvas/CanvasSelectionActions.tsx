import { CanvasActionBar } from '@/widgets/map/ui/CanvasActionBar';
import { IconButton } from '@green/ui';
import { Copy, LockKeyhole, Move, Sprout } from 'lucide-react';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasSelectionActionsProps extends Pick<
  WorkspaceReadyModel,
  | 'activateTool'
  | 'changePreview'
  | 'editorBusy'
  | 'openRightPanel'
  | 'planLocked'
  | 'previewSelectionLock'
  | 'sceneOpen'
  | 'selectedIds'
  | 'selectedLocked'
  | 'selectedObjects'
  | 'setActiveLayerId'
  | 'setPanel'
  | 'setSpeciesAssignmentOpen'
  | 'tool'
> {}
export const CanvasSelectionActionsPropsFor = (
  props: CanvasSelectionActionsProps,
) => ({
  activateTool: props.activateTool,
  changePreview: props.changePreview,
  editorBusy: props.editorBusy,
  openRightPanel: props.openRightPanel,
  planLocked: props.planLocked,
  previewSelectionLock: props.previewSelectionLock,
  sceneOpen: props.sceneOpen,
  selectedIds: props.selectedIds,
  selectedLocked: props.selectedLocked,
  selectedObjects: props.selectedObjects,
  setActiveLayerId: props.setActiveLayerId,
  setPanel: props.setPanel,
  setSpeciesAssignmentOpen: props.setSpeciesAssignmentOpen,
  tool: props.tool,
});
export const CanvasSelectionActions: FC<CanvasSelectionActionsProps> = ({
  activateTool,
  changePreview,
  editorBusy,
  openRightPanel,
  planLocked,
  previewSelectionLock,
  sceneOpen,
  selectedIds,
  selectedLocked,
  selectedObjects,
  setActiveLayerId,
  setPanel,
  setSpeciesAssignmentOpen,
  tool,
}) => (
  <>
    {!sceneOpen &&
      selectedIds.length > 0 &&
      !changePreview &&
      ['select', 'move', 'copy', 'select_box', 'select_lasso'].includes(
        tool,
      ) && (
        <CanvasActionBar role="group" aria-label="Действия над выделением">
          <span>{selectedIds.length}</span>
          <IconButton
            icon={Sprout}
            label="Назначить виды"
            variant="ghost"
            disabled={editorBusy || planLocked || selectedLocked}
            onClick={() => {
              setActiveLayerId(undefined);
              setPanel(null);
              setSpeciesAssignmentOpen(true);
              openRightPanel();
            }}
          />
          <IconButton
            icon={Move}
            label="Переместить выбранное"
            variant="ghost"
            disabled={editorBusy || selectedObjects.some((o) => o.locked)}
            onClick={() => activateTool('move')}
          />
          <IconButton
            icon={Copy}
            label="Копировать выбранное"
            variant="ghost"
            disabled={editorBusy}
            onClick={() => activateTool('copy')}
          />
          <IconButton
            icon={LockKeyhole}
            label={
              selectedLocked ? 'Открепить выбранное' : 'Закрепить выбранное'
            }
            variant="ghost"
            disabled={editorBusy}
            onClick={() => previewSelectionLock(!selectedLocked)}
          />
        </CanvasActionBar>
      )}
  </>
);
