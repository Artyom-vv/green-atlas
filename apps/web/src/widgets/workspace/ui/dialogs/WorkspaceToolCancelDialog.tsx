import { Button, Dialog, FormActions } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceToolCancelDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'pendingTool'
  | 'discardZoneDrawing'
  | 'setPendingTool'
  | 'assistant'
  | 'previewPattern'
  | 'previewRecommendation'
  | 'previewBrush'
  | 'setBrushStrokes'
  | 'setRowAxis'
  | 'setRowAxisSource'
  | 'previewChanges'
  | 'setSpeciesAssignmentOpen'
  | 'setRecommendationOpen'
  | 'setPanel'
  | 'setActiveLayerId'
  | 'editor'
  | 'selectedPatternZoneIds'
  | 'plantingZoneIds'
  | 'setSelectedPatternZoneIds'
> {}

export const WorkspaceToolCancelDialogPropsFor = (
  model: WorkspaceToolCancelDialogProps,
): WorkspaceToolCancelDialogProps => ({
  pendingTool: model.pendingTool,
  discardZoneDrawing: model.discardZoneDrawing,
  setPendingTool: model.setPendingTool,
  assistant: model.assistant,
  previewPattern: model.previewPattern,
  previewRecommendation: model.previewRecommendation,
  previewBrush: model.previewBrush,
  setBrushStrokes: model.setBrushStrokes,
  setRowAxis: model.setRowAxis,
  setRowAxisSource: model.setRowAxisSource,
  previewChanges: model.previewChanges,
  setSpeciesAssignmentOpen: model.setSpeciesAssignmentOpen,
  setRecommendationOpen: model.setRecommendationOpen,
  setPanel: model.setPanel,
  setActiveLayerId: model.setActiveLayerId,
  editor: model.editor,
  selectedPatternZoneIds: model.selectedPatternZoneIds,
  plantingZoneIds: model.plantingZoneIds,
  setSelectedPatternZoneIds: model.setSelectedPatternZoneIds,
});

export const WorkspaceToolCancelDialog: FC<WorkspaceToolCancelDialogProps> = ({
  pendingTool,
  discardZoneDrawing,
  setPendingTool,
  assistant,
  previewPattern,
  previewRecommendation,
  previewBrush,
  setBrushStrokes,
  setRowAxis,
  setRowAxisSource,
  previewChanges,
  setSpeciesAssignmentOpen,
  setRecommendationOpen,
  setPanel,
  setActiveLayerId,
  editor,
  selectedPatternZoneIds,
  plantingZoneIds,
  setSelectedPatternZoneIds,
}) => {
  return (
    <Dialog
      open={Boolean(pendingTool)}
      title="Отменить текущий предпросмотр?"
      onClose={() => setPendingTool(undefined)}
      footer={
        <FormActions layout="equal" minItemWidth="12rem">
          <Button variant="secondary" onClick={() => setPendingTool(undefined)}>
            Остаться
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              if (!pendingTool) return;
              discardZoneDrawing();
              assistant.discard();
              previewPattern.cancel();
              previewRecommendation.cancel();
              previewBrush.cancel();
              previewPattern.reset();
              previewRecommendation.reset();
              previewBrush.reset();
              setBrushStrokes([]);
              setRowAxis(undefined);
              setRowAxisSource(undefined);
              previewChanges.reset();
              setSpeciesAssignmentOpen(false);
              setRecommendationOpen(false);
              setPanel(null);
              setActiveLayerId(undefined);
              editor.setTool(pendingTool);
              if (
                !selectedPatternZoneIds.length &&
                plantingZoneIds.length === 1
              )
                setSelectedPatternZoneIds(plantingZoneIds);
              setPendingTool(undefined);
            }}
          >
            Отменить и перейти
          </Button>
        </FormActions>
      }
    >
      <p>
        Посадки ещё не изменены. Новый инструмент начнёт отдельную операцию.
      </p>
    </Dialog>
  );
};
