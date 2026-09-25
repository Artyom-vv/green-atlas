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
import { PlacementCandidateDetails } from './PlacementCandidateDetails';
import { SearchDomainSummary } from './SearchDomainSummary';
import { GeometryInspectionPanel } from './GeometryInspectionPanel';
import {
  patternResultHeading,
  patternResultReasons,
} from '../model/patternResultPresentation';
import type { CandidateInspection } from '../model/useCandidateInspection';

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
}) => (
  <section className="grid gap-3" aria-label="Результат расчёта">
    <h3 className="m-0 text-sm font-semibold">
      {patternResultHeading(preview)}
    </h3>
    <dl className="m-0 grid gap-2 text-xs" aria-label="Параметры результата">
      {!preview.accepted_count && (
        <div className="flex justify-between gap-3">
          <dt className="text-neutral-600">Запрошено</dt>
          <dd className="m-0 tabular-nums">{preview.requested_count}</dd>
        </div>
      )}
      {preview.generated_count != null && (
        <div className="flex justify-between gap-3">
          <dt className="text-neutral-600">Проверено позиций</dt>
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
    <GeometryInspectionPanel inspection={inspection} />
    {preview.search_stop_reason &&
      preview.search_stop_reason !== 'target_reached' && (
        <p className="m-0 text-xs text-neutral-600" role="status">
          {preview.search_stop_reason === 'time_limit'
            ? 'Достигнут лимит времени поиска'
            : preview.search_stop_reason === 'domain_exhausted'
              ? preview.search_domains?.some(
                  (domain) => (domain.pending_area_m2 ?? 0) > 0,
                )
                ? 'Поиск ожидает завершения проверки области'
                : preview.search_domains?.some(
                      (domain) => domain.unresolved_area_m2 > 0,
                    )
                  ? 'Для поиска нужна подтверждённая область'
                  : 'В подготовленной области нет позиций для выбранных условий'
              : 'Проверена выборка, не весь участок'}
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
      !!patternResultReasons(preview).length && (
        <Disclosure title="Причины недобора">
          <ul className="m-0 grid gap-2 pl-4 text-xs leading-4">
            {patternResultReasons(preview).map((item) => (
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
