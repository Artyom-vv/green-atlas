import type { FC } from 'react';
import { cx } from '@green/ui';
import { GrowthHorizonControl } from '@/entities/planting-forecast/ui/GrowthHorizonControl';
import {
  CanvasControlSlot,
  canvasSurfaceClassName,
} from '@/shared/ui/CanvasControls';
import type { SceneReviewOptions } from '../model/sceneContracts';

export interface SceneForecastControlProps extends Pick<
  SceneReviewOptions,
  'horizon' | 'onHorizon'
> {
  visible: boolean;
}

export const SceneForecastControl: FC<SceneForecastControlProps> = ({
  visible,
  horizon,
  onHorizon,
}) => (
  <CanvasControlSlot
    position="bottomCenter"
    className="hidden:hidden"
    role="group"
    aria-label="Прогноз роста в 3D"
    hidden={!visible}
  >
    <GrowthHorizonControl
      appearance="viewport"
      className={cx(canvasSurfaceClassName, 'w-96 max-w-full')}
      value={horizon}
      showMetrics={false}
      onChange={(next) => {
        if (next !== undefined) onHorizon(next);
      }}
    />
  </CanvasControlSlot>
);
