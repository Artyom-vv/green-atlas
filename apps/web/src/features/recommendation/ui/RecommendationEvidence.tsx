import type { FC } from 'react';
import type { RecommendationPreview } from '@green/api-client';
import { InlineMessage } from '@green/ui';
import { evidenceLabel } from '../model/recommendationSummary';
import { RecommendationZoneEvidence } from './RecommendationZoneEvidence';

export interface RecommendationEvidenceProps {
  proposal: RecommendationPreview;
  requestedCount?: number | null;
  speciesNames?: Map<string, string>;
  zoneNames?: Map<string, string>;
}
export const RecommendationEvidence: FC<RecommendationEvidenceProps> = ({
  proposal,
  requestedCount,
  speciesNames,
  zoneNames,
}) => {
  const first = proposal.explanations[0];
  return (
    <div className="grid min-w-0 gap-4">
      {requestedCount != null && (
        <p className="m-0">Максимум по заданию: {requestedCount}</p>
      )}
      <RecommendationZoneEvidence
        proposal={proposal}
        speciesNames={speciesNames}
        zoneNames={zoneNames}
      />
      <section className="grid gap-3">
        <h3 className="m-0 text-sm font-semibold">На чём основано</h3>
        <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-2 text-sm">
          <dt>Геометрия DXF</dt>
          <dd className="m-0 text-right">
            {evidenceLabel(proposal.evidence.spatial_constraints)}
          </dd>
          <dt>Каталог пород</dt>
          <dd className="m-0 text-right">
            {evidenceLabel(proposal.evidence.species_catalog)}
          </dd>
        </dl>
        <p className="m-0 text-neutral-600">{proposal.evidence.note}</p>
      </section>
      {!!first?.hard_constraints?.length && (
        <ul className="m-0 grid gap-2 pl-5">
          {first.hard_constraints.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      {!!first?.biological_risks?.length && (
        <InlineMessage tone="warning">
          {first.biological_risks[0]}
          {first.biological_risks.length > 1
            ? ` Ещё: ${first.biological_risks.length - 1}.`
            : ''}
        </InlineMessage>
      )}
      {!!proposal.data_gaps?.length && (
        <section className="grid gap-2">
          <h3 className="m-0 text-sm font-semibold">Чего пока не знаем</h3>
          <p className="m-0">{proposal.data_gaps.join(', ')}.</p>
        </section>
      )}
      {!!proposal.skipped.length && (
        <InlineMessage tone="info">
          Не включено позиций: {proposal.skipped.length}.{' '}
          {proposal.skipped[0]?.reason}
        </InlineMessage>
      )}
    </div>
  );
};
