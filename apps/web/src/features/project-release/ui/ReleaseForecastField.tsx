import type { FC } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import {
  GrowthHorizonSlider,
  type GrowthHorizon,
} from '@/entities/planting-forecast/ui/GrowthHorizonControl';
import { horizonLabel, type ReleaseFormValues } from '../model/releaseForm';

export interface ReleaseForecastFieldProps {
  onGrowthHorizon: (value: GrowthHorizon) => void;
}

export const ReleaseForecastField: FC<ReleaseForecastFieldProps> = ({
  onGrowthHorizon,
}) => {
  const { control, setValue } = useFormContext<ReleaseFormValues>();
  const sceneHorizon = useWatch({ control, name: 'sceneHorizon' });
  return (
    <section aria-label="Прогноз в пакете" className="grid gap-2">
      <div className="flex items-center justify-between gap-4">
        <span>Прогноз в пакете</span>
        <output className="text-neutral-600 tabular-nums">
          {horizonLabel(sceneHorizon)}
        </output>
      </div>
      <GrowthHorizonSlider
        ariaLabel="Горизонт прогноза в пакете"
        value={sceneHorizon}
        onChange={(value) => {
          setValue('sceneHorizon', value ?? 0, { shouldDirty: true });
          onGrowthHorizon(value);
        }}
      />
    </section>
  );
};
