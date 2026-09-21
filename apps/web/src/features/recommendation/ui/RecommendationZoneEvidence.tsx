import type { RecommendationPreview } from '@green/api-client';
import { territoryLabels } from '@/entities/species/model/assortmentLabels';

export interface RecommendationZoneEvidenceProps {
  proposal: RecommendationPreview;
  speciesNames?: Map<string, string>;
  zoneNames?: Map<string, string>;
}

const assortmentLabels = {
  listed: 'В ассортименте',
  not_recommended: 'Не рекомендовано для этой территории',
  individual_review: 'Требуется индивидуальное согласование',
  unreviewed: 'Строка ассортимента не подтверждена',
};

/** Explain each saved zone from the server result; never recompute eligibility. */
export function RecommendationZoneEvidence({
  proposal,
  speciesNames,
  zoneNames,
}: RecommendationZoneEvidenceProps) {
  if (!proposal.zone_results?.length) return null;
  return (
    <section className="grid gap-4" aria-label="Подбор по участкам">
      <h3 className="m-0 text-sm font-semibold">Подбор по участкам</h3>
      {proposal.zone_results.map((zone) => {
        const chosen = new Map<string, number>();
        for (const plant of proposal.change_set?.additions ?? []) {
          if (
            plant.planting_zone_id === zone.zone_id &&
            plant.species_revision_id
          ) {
            chosen.set(
              plant.species_revision_id,
              (chosen.get(plant.species_revision_id) ?? 0) + 1,
            );
          }
        }
        return (
          <section
            key={zone.zone_id}
            className="grid gap-2 border-t border-neutral-200 pt-3"
          >
            <h4 className="m-0 text-sm font-semibold">
              {zoneNames?.get(zone.zone_id) ?? zone.zone_id}
            </h4>
            <p className="m-0 text-xs">
              {territoryLabels[zone.territory.category]}. Подобрано{' '}
              {zone.accepted_count} из {zone.requested_count}.
            </p>
            <p className="m-0 text-xs text-neutral-600">
              Основание: {zone.territory.basis}
            </p>
            {Array.from(chosen, ([id, count]) => (
              <p className="m-0 text-sm" key={id}>
                {speciesNames?.get(id) ?? id}: {count}
              </p>
            ))}
            <p className="m-0 text-xs leading-5 text-neutral-600">
              {zone.selection_reason}
            </p>
            <details className="text-xs">
              <summary className="cursor-pointer py-1">
                Проверенные растения ({zone.species_options?.length ?? 0})
              </summary>
              <dl className="m-0 grid gap-3 pt-3">
                {(zone.species_options ?? []).map((option) => (
                  <div key={option.species_revision_id} className="grid gap-1">
                    <dt className="font-medium">
                      {speciesNames?.get(option.species_revision_id) ??
                        option.species_revision_id}
                    </dt>
                    <dd className="m-0 text-neutral-600">
                      {assortmentLabels[option.assortment_status]}
                      {option.site_suitability?.checks
                        .filter(
                          (check) =>
                            check.status === 'documented_conflict' ||
                            check.status === 'unknown',
                        )
                        .map((check) => (
                          <p className="my-1" key={check.dimension}>
                            {check.reason}
                          </p>
                        ))}
                      {option.assortment_status === 'listed' &&
                        (!option.site_suitability ||
                          option.site_suitability.status ===
                            'documented_match') && (
                          <span className="block">
                            Допустимых мест: {option.accepted_count ?? 0}
                          </span>
                        )}
                      {option.source_url && (
                        <a
                          className="mt-1 inline-block text-blue-700 underline"
                          href={`${option.source_url}#page=${option.source_page}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Таблица Москвы, стр. {option.source_page}, строка{' '}
                          {option.source_row}
                        </a>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </details>
          </section>
        );
      })}
    </section>
  );
}
