import type { FC } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import { Disclosure } from '@green/ui';
import {
  GrowthHorizonControl,
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
    <Disclosure
      variant="plain"
      title={
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span>Прогноз в пакете</span>
          <span className="font-normal text-neutral-600 tabular-nums">
            {horizonLabel(sceneHorizon)}
          </span>
        </span>
      }
    >
      <GrowthHorizonControl
        value={sceneHorizon}
        onChange={(value) => {
          setValue('sceneHorizon', value ?? 0, { shouldDirty: true });
          onGrowthHorizon(value);
        }}
        showMetrics={false}
      />
    </Disclosure>
  );
};
