import { CanvasActionBar } from '@/widgets/map/ui/CanvasActionBar';
import { Button } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasDraftActionsProps extends Pick<
  WorkspaceReadyModel,
  | 'patternPreview'
  | 'patternSettingsOpen'
  | 'pendingZone'
  | 'recommendationPreview'
  | 'setPatternSettingsOpen'
  | 'setZoneReviewOpen'
  | 'tool'
  | 'zoneReviewOpen'
  | 'zoneDrawingMode'
  | 'placementAreaDrawing'
  | 'cancelZoneDrawing'
> {}
export const CanvasDraftActionsPropsFor = (props: CanvasDraftActionsProps) => ({
  patternPreview: props.patternPreview,
  patternSettingsOpen: props.patternSettingsOpen,
  pendingZone: props.pendingZone,
  recommendationPreview: props.recommendationPreview,
  setPatternSettingsOpen: props.setPatternSettingsOpen,
  setZoneReviewOpen: props.setZoneReviewOpen,
  tool: props.tool,
  zoneReviewOpen: props.zoneReviewOpen,
  zoneDrawingMode: props.zoneDrawingMode,
  placementAreaDrawing: props.placementAreaDrawing,
  cancelZoneDrawing: props.cancelZoneDrawing,
});
export const CanvasDraftActions: FC<CanvasDraftActionsProps> = ({
  patternPreview,
  patternSettingsOpen,
  pendingZone,
  recommendationPreview,
  setPatternSettingsOpen,
  setZoneReviewOpen,
  tool,
  zoneReviewOpen,
  zoneDrawingMode,
  placementAreaDrawing,
  cancelZoneDrawing,
}) => (
  <>
    {(zoneDrawingMode || placementAreaDrawing) && (
      <CanvasActionBar align="start">
        <strong>
          {placementAreaDrawing || zoneDrawingMode === 'new'
            ? 'Новый участок'
            : 'Изменение контура'}
        </strong>
        <Button variant="secondary" onClick={cancelZoneDrawing}>
          Отменить рисование
        </Button>
      </CanvasActionBar>
    )}
    {pendingZone && !zoneReviewOpen && (
      <CanvasActionBar align="start">
        <strong>Новый контур не сохранён</strong>
        <Button variant="primary" onClick={() => setZoneReviewOpen(true)}>
          Проверить участок
        </Button>
      </CanvasActionBar>
    )}
    {tool === 'pattern_fill' && !patternSettingsOpen && (
      <CanvasActionBar align="start">
        <Button variant="primary" onClick={() => setPatternSettingsOpen(true)}>
          {patternPreview || recommendationPreview
            ? 'Вернуться к результату'
            : 'Продолжить размещение'}
        </Button>
      </CanvasActionBar>
    )}
  </>
);
