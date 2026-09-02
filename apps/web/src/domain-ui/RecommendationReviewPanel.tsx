import type { RecommendationPreview } from '@green/api-client';
import { Button, InlineMessage } from '@green/ui';
import { InspectorHeader } from './InspectorHeader';

const evidenceLabel = (status: 'verified' | 'partial' | 'missing') => status === 'verified' ? 'проверены' : status === 'partial' ? 'частично' : 'нет данных';

export function RecommendationReviewPanel({ proposal, applying, onApply, onCancel }: {
  proposal: RecommendationPreview;
  applying?: boolean;
  onApply: () => void;
  onCancel: () => void;
}) {
  const count = proposal.change_set?.additions?.length ?? 0;
  const first = proposal.explanations[0];
  return <div className="project-inspector recommendation-review">
    <InspectorHeader title="Предложение готово" meta={`${count} допустимых позиций`} />
    <div className="recommendation-review__content">
      <section>
        <h3>На чём основано</h3>
        <dl><dt>Геометрия DXF</dt><dd>{evidenceLabel(proposal.evidence.spatial_constraints)}</dd><dt>Каталог пород</dt><dd>{evidenceLabel(proposal.evidence.species_catalog)}</dd></dl>
        <p>{proposal.evidence.note}</p>
      </section>
      {first ? <section>
        <h3>Почему позиции допустимы</h3>
        <ul>{(first.hard_constraints ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
      </section> : null}
      {first?.biological_risks?.length ? <InlineMessage tone="warning">{first.biological_risks[0]}{first.biological_risks.length > 1 ? ` Ещё: ${first.biological_risks.length - 1}.` : ''}</InlineMessage> : null}
      <section>
        <h3>Чего пока не знаем</h3>
        <p>{(proposal.data_gaps ?? []).join(', ')}.</p>
      </section>
      {proposal.skipped.length ? <InlineMessage tone="info">Не включено позиций: {proposal.skipped.length}. {proposal.skipped[0]?.reason}</InlineMessage> : null}
      {!proposal.change_set ? <InlineMessage tone="warning">Допустимых позиций не найдено. Измените участки или приоритет.</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <footer><Button variant="secondary" disabled={applying} onClick={onCancel}>Назад</Button><Button variant="primary" loading={applying} disabled={!proposal.change_set?.can_apply} onClick={onApply}>Применить</Button></footer>
  </div>;
}
