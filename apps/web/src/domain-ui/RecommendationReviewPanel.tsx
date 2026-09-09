import type { RecommendationPreview } from '@green/api-client';
import { useState } from 'react';
import { Button, Dialog, InlineMessage } from '@green/ui';
import { WorkflowSteps } from './WorkflowSteps';
import { EditorActions, EditorPanel } from './EditorPanel';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';
import { plantingCount } from './countLabel';

const evidenceLabel = (status: 'verified' | 'partial' | 'missing') => status === 'verified' ? 'проверены' : status === 'partial' ? 'частично' : 'нет данных';

export function RecommendationReviewPanel({ proposal, requestedCount, speciesNames, applying, growthHorizon, onGrowthHorizon, onApply, onCancel }: {
  proposal: RecommendationPreview;
  requestedCount?: number | null;
  speciesNames?: Map<string, string>;
  applying?: boolean;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (value: GrowthHorizon) => void;
  onApply: () => void;
  onCancel: () => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const count = proposal.change_set?.additions?.length ?? 0;
  const first = proposal.explanations[0];
  const composition = new Map<string, number>();
  for (const item of proposal.change_set?.additions ?? []) {
    const name = speciesNames?.get(item.species_revision_id ?? '') ?? (item.kind === 'tree' ? 'Дерево' : 'Кустарник');
    composition.set(name, (composition.get(name) ?? 0) + 1);
  }
  return <EditorPanel title="Предложение готово">
    <WorkflowSteps labels={['Участки', 'Задача', 'Проверка']} current={2} label="Шаги подбора" />
    <div className="pattern-body">
    <h3>{plantingCount(count)}</h3>
    {composition.size ? <p className="proposal-composition" aria-label="Состав предложения">{[...composition].map(([name, amount]) => composition.size === 1 ? name : `${name}: ${amount}`).join(', ')}</p> : null}
    {count > 0 && onGrowthHorizon ? <GrowthHorizonControl value={growthHorizon} forecasts={proposal.change_set?.additions ?? []} onChange={onGrowthHorizon} showMetrics={false} /> : null}
    {proposal.arrangement === 'building_screen' && count > 0 ? <p className="editor-panel__hint">Скрытие фасадов не оценивалось.</p> : null}
    {proposal.data_gaps?.length ? <p className="editor-panel__hint">В исходных данных есть пробелы.</p> : null}
    <Button variant="secondary" onClick={() => setDetailsOpen(true)}>Что учтено</Button>
    <Dialog open={detailsOpen} title="Что учтено в предложении" onClose={() => setDetailsOpen(false)}>
      {requestedCount != null ? <p>Максимум по заданию: {requestedCount}</p> : null}
      <section>
        <h3>На чём основано</h3>
        <dl className="editor-panel__metrics"><dt>Геометрия DXF</dt><dd>{evidenceLabel(proposal.evidence.spatial_constraints)}</dd><dt>Каталог пород</dt><dd>{evidenceLabel(proposal.evidence.species_catalog)}</dd></dl>
        <p>{proposal.evidence.note}</p>
      </section>
      {first ? <ul className="editor-problem-list">{(first.hard_constraints ?? []).map(item => <li key={item}>{item}</li>)}</ul> : null}
      {first?.biological_risks?.length ? <InlineMessage tone="warning">{first.biological_risks[0]}{first.biological_risks.length > 1 ? ` Ещё: ${first.biological_risks.length - 1}.` : ''}</InlineMessage> : null}
      {proposal.data_gaps?.length ? <section className="editor-panel__section">
        <h3>Чего пока не знаем</h3>
        <p>{(proposal.data_gaps ?? []).join(', ')}.</p>
      </section> : null}
      {proposal.skipped.length ? <InlineMessage tone="info">Не включено позиций: {proposal.skipped.length}. {proposal.skipped[0]?.reason}</InlineMessage> : null}
    </Dialog>
      {!count ? <InlineMessage tone="warning">Здесь не найдено свободных мест для этой схемы. Выберите другие участки или измените задачу.</InlineMessage> : null}
    </div>
    <EditorActions><Button variant="secondary" disabled={applying} onClick={onCancel}>Изменить условия</Button><Button variant="primary" loading={applying} disabled={!proposal.change_set?.can_apply || !count} onClick={onApply}>Добавить {count}</Button></EditorActions>
  </EditorPanel>;
}
