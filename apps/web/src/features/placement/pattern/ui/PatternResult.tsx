import type { FC } from 'react';
import type {
  PatternPreview,
  PlantingZoneAssignment,
  SpeciesRevision,
} from '@green/api-client';
import { Button, Disclosure, FormActions } from '@green/ui';
import {
  GrowthHorizonControl,
  growthHorizonLabel,
  type GrowthHorizon,
} from '@/entities/planting-forecast';
import { PlacementAllocation } from '../../inspection/ui/PlacementAllocation';
import { PlacementCandidateDetails } from '../../inspection/ui/PlacementCandidateDetails';
import { SearchDomainSummary } from '../../inspection/ui/SearchDomainSummary';
import {
  patternResultHeading,
  patternResultReasons,
  patternSearchNotice,
} from '../../inspection/model/patternResultPresentation';
import type { CandidateInspection } from '../../inspection/model/useCandidateInspection';

export interface PatternResultProps {
  preview: PatternPreview;
  zones: PlantingZoneAssignment[];
  selectedZoneIds: string[];
  selectedSpecies?: SpeciesRevision;
  compactAlternative?: SpeciesRevision;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (year: GrowthHorizon) => void;
  onAlternative: (id: string) => void;
  inspection?: CandidateInspection;
}

/** Объясняет полученный вариант и адресные причины отклонения его позиций. */
export const PatternResult: FC<PatternResultProps> = ({
  preview,
  zones,
  selectedZoneIds,
  selectedSpecies,
  compactAlternative,
  growthHorizon,
  onGrowthHorizon,
  onAlternative,
  inspection,
}) => {
  const searchNotice = patternSearchNotice(preview);
  const reasons = patternResultReasons(preview);
  return (
    <section className="grid gap-3" aria-label="Результат расчёта">
      <h3 className="m-0 text-sm font-semibold">
        {patternResultHeading(preview)}
      </h3>
      <dl className="m-0 grid gap-2 text-xs" aria-label="Параметры результата">
        <div className="flex justify-between gap-3">
          <dt className="text-neutral-600">Горизонт расчёта</dt>
          <dd className="m-0 whitespace-nowrap tabular-nums">
            {growthHorizonLabel(preview.planning_horizon_year)}
          </dd>
        </div>
        {!preview.accepted_count && (
          <div className="flex justify-between gap-3">
            <dt className="text-neutral-600">Запрошено</dt>
            <dd className="m-0 tabular-nums">{preview.requested_count}</dd>
          </div>
        )}
        {preview.generated_count != null && (
          <div className="flex justify-between gap-3">
            <dt className="text-neutral-600">Рассмотрено позиций</dt>
            <dd className="m-0 tabular-nums">{preview.generated_count}</dd>
          </div>
        )}
        {!!preview.effective_spacing_m && (
          <div className="flex justify-between gap-3">
            <dt className="text-neutral-600">Шаг между растениями</dt>
            <dd className="m-0 whitespace-nowrap tabular-nums">
              {preview.effective_spacing_m.toLocaleString('ru-RU')} м
            </dd>
          </div>
        )}
      </dl>
      <SearchDomainSummary domains={preview.search_domains} />
      {searchNotice && (
        <p className="m-0 text-xs text-neutral-600" role="status">
          {searchNotice}
        </p>
      )}
      {preview.accepted_count > 0 && selectedSpecies && onGrowthHorizon && (
        <section aria-label="Прогноз на карте">
          <GrowthHorizonControl
            label="Просмотр роста"
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
      {preview.accepted_count < preview.requested_count && !!reasons.length && (
        <Disclosure title="Причины недобора">
          <ul className="m-0 grid gap-2 pl-4 text-xs leading-4">
            {reasons.map((item) => (
              <li key={item.key}>{item.message}</li>
            ))}
          </ul>
        </Disclosure>
      )}
      <PlacementCandidateDetails
        key={preview.pattern_id}
        skipped={preview.skipped ?? []}
        inspection={inspection}
        spacing={preview.effective_spacing_m ?? 0}
      />
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
};
