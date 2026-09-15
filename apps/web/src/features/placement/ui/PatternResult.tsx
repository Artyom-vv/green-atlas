import type { FC } from 'react';
import type {
  PatternPreview,
  PlantingZoneAssignment,
  SpeciesRevision,
} from '@green/api-client';
import { Button, Disclosure, FormActions } from '@green/ui';
import {
  GrowthHorizonControl,
  type GrowthHorizon,
} from '@/entities/planting-forecast';
import { PlacementAllocation } from './PlacementAllocation';

export interface PatternResultProps {
  preview: PatternPreview;
  zones: PlantingZoneAssignment[];
  selectedZoneIds: string[];
  selectedSpecies?: SpeciesRevision;
  compactAlternative?: SpeciesRevision;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (year: GrowthHorizon) => void;
  onAlternative: (id: string) => void;
}

export const PatternResult: FC<PatternResultProps> = ({
  preview,
  zones,
  selectedZoneIds,
  selectedSpecies,
  compactAlternative,
  growthHorizon,
  onGrowthHorizon,
  onAlternative,
}) => (
  <section className="grid gap-3" aria-label="Результат расчёта">
    <h3 className="m-0 text-sm font-semibold">
      {preview.accepted_count
        ? `Найдено ${preview.accepted_count} из ${preview.requested_count}`
        : 'Мест не найдено'}
    </h3>
    {!preview.accepted_count && (
      <p className="m-0 text-xs text-neutral-600">
        Запрошено: {preview.requested_count}
      </p>
    )}
    {!!preview.effective_spacing_m && (
      <p className="m-0 text-xs text-neutral-600">
        Минимальное расстояние: {preview.effective_spacing_m} м
      </p>
    )}
    {preview.accepted_count > 0 && selectedSpecies && onGrowthHorizon && (
      <section aria-label="Прогноз на карте">
        <GrowthHorizonControl
          value={growthHorizon}
          forecasts={preview.change_set?.additions ?? []}
          onChange={onGrowthHorizon}
        />
      </section>
    )}
    {selectedZoneIds.length > 1 && (
      <PlacementAllocation
        zones={zones}
        selectedIds={selectedZoneIds}
        preview={preview}
      />
    )}
    {preview.accepted_count < preview.requested_count &&
      !!preview.reason_summary?.length && (
        <Disclosure title="Почему меньше">
          <ul className="m-0 grid gap-2 pl-4 text-xs leading-4">
            {preview.reason_summary.map((item, index) => (
              <li key={`${item.status}:${item.code}:${index}`}>
                {item.count} — {item.message}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
    {!!preview.unverified_data?.length && (
      <Disclosure title="Ограничения проверки">
        <ul className="m-0 grid gap-2 pl-4 text-xs leading-4">
          {preview.unverified_data.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      </Disclosure>
    )}
    {!preview.accepted_count && compactAlternative && (
      <FormActions layout="equal">
        <Button
          variant="secondary"
          onClick={() => onAlternative(compactAlternative.id)}
        >
          Выбрать {compactAlternative.common_name}
        </Button>
      </FormActions>
    )}
  </section>
);
