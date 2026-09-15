import type { FC } from 'react';
import { GrowthHorizonControl } from '@/entities/planting-forecast/ui/GrowthHorizonControl';
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
  <div
    className="rounded-card hidden:hidden absolute bottom-4 left-1/2 z-4 flex min-h-9 w-[clamp(14rem,32%,24rem)] max-w-[calc(100%-10rem)] -translate-x-1/2 items-center gap-2 border border-neutral-300 bg-white px-3 py-2 text-xs shadow-sm"
    role="group"
    aria-label="Возраст посадок в 3D"
    hidden={!visible}
  >
    <GrowthHorizonControl
      className="w-full p-0"
      value={horizon}
      showMetrics={false}
      onChange={(next) => {
        if (next !== undefined) onHorizon(next);
      }}
    />
  </div>
);
