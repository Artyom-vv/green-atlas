import type { FC } from 'react';
import type { PlanObject } from '@green/api-client';
import {
  GrowthHorizonControl,
  type GrowthHorizon,
} from './GrowthHorizonControl';

export interface PlantingGrowthProps {
  objects: PlanObject[];
  value: GrowthHorizon;
  onChange: (value: GrowthHorizon) => void;
  showControl?: boolean;
}
export const EditorGrowth: FC<PlantingGrowthProps> = ({
  objects,
  value,
  onChange,
  showControl = true,
}) => {
  const missingSpecies = objects.filter(
    (object) => !object.species_revision_id,
  ).length;
  return (
    <GrowthHorizonControl
      forecasts={objects}
      value={value}
      onChange={onChange}
      showSlider={showControl}
      missingReason={
        missingSpecies
          ? `Без породы: ${missingSpecies} из ${objects.length}. Назначьте вид, чтобы рассчитать рост.`
          : undefined
      }
    />
  );
};
