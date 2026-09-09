import type { PlanObject } from '@green/api-client';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';

export function EditorGrowth({ objects, value, onChange, showControl = true }: { objects: PlanObject[]; value: GrowthHorizon; onChange: (value: GrowthHorizon) => void; showControl?: boolean }) {
  const missingSpecies = objects.filter(object => !object.species_revision_id).length;
  return <GrowthHorizonControl forecasts={objects} value={value} onChange={onChange} showSlider={showControl} missingReason={missingSpecies ? `Без породы: ${missingSpecies} из ${objects.length}. Назначьте вид, чтобы рассчитать рост.` : undefined} />;
}
