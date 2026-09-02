import type { ChangeSetPreview } from '@green/api-client';
import { Button, InlineMessage, StepProgress } from '@green/ui';
import { InspectorHeader } from './InspectorHeader';
import { InspectorFooter } from './InspectorLayout';
import { PLANTING_WORKFLOW_STEPS } from './plantingWorkflow';

export function ChangeSetReviewPanel({ preview, applying, note, rejectedReasons = [], unverifiedData = [], onApply, onCancel }: { preview: ChangeSetPreview; applying?: boolean; note?: string; rejectedReasons?: string[]; unverifiedData?: string[]; onApply: () => void; onCancel: () => void }) {
  const additions = preview.additions?.length ?? 0;
  const updates = preview.updates?.length ?? 0;
  const deletions = preview.deletion_ids?.length ?? 0;
  const blocked = preview.candidate_results?.filter((item) => item.status === 'blocked') ?? [];
  const reviewRequired = preview.candidate_results?.filter((item) => item.status === 'unknown' || item.status === 'soft_conflict') ?? [];
  const rejectionSummary = [...rejectedReasons.reduce((counts, reason) => counts.set(reason, (counts.get(reason) ?? 0) + 1), new Map<string, number>())]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3);
  return <div className="project-inspector change-set-review">
    <InspectorHeader title={preview.label} meta="Предпросмотр изменений" />
    <section className="change-set-review__summary">
      <StepProgress current={2} steps={PLANTING_WORKFLOW_STEPS} />
      <h3>Проверьте схему</h3>
      <dl>
        {additions ? <><dt>Новых посадок</dt><dd>{additions}</dd></> : null}
        {updates ? <><dt>Изменено</dt><dd>{updates}</dd></> : null}
        {deletions ? <><dt>Удалено</dt><dd>{deletions}</dd></> : null}
      </dl>
      {blocked.length ? <InlineMessage tone="error" title="Перемещение недоступно">{blocked[0].reason}{blocked[0].suggested_action ? ` ${blocked[0].suggested_action}` : ''}{blocked.length > 1 ? ` Ещё ${blocked.length - 1}` : ''}</InlineMessage> : null}
      {!blocked.length && reviewRequired.length ? <InlineMessage tone="warning" title="Нужна проверка">{reviewRequired[0].reason}{reviewRequired[0].suggested_action ? ` ${reviewRequired[0].suggested_action}` : ''}{reviewRequired.length > 1 ? ` Ещё ${reviewRequired.length - 1}` : ''}</InlineMessage> : null}
      {!blocked.length && !reviewRequired.length ? <p>Пунктиром показан результат до сохранения</p> : null}
      {note ? <InlineMessage tone="info">{note}</InlineMessage> : null}
      {unverifiedData.length ? <InlineMessage tone="warning">Нет данных: {unverifiedData.join(', ')}</InlineMessage> : null}
      {additions ? <div className="change-set-review__checks"><strong>Учтено при расчёте</strong><span>Границы участков, распознанные объекты DXF, нормативные отступы, прогноз кроны и корней</span></div> : null}
      {rejectionSummary.length ? <div className="change-set-review__checks"><strong>Исключено при поиске</strong><ul>{rejectionSummary.map(([reason, count]) => <li key={reason}>{count}: {reason}</li>)}</ul></div> : null}
    </section>
    <div className="inspector-spacer" />
    <InspectorFooter><Button variant="secondary" disabled={applying} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={applying} disabled={!preview.can_apply} onClick={onApply}>{additions ? `Добавить ${additions}` : 'Применить'}</Button></InspectorFooter>
  </div>;
}
