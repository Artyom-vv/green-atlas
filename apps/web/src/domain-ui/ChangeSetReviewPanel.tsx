import type { ChangeSetPreview } from '@green/api-client';
import { Button, InlineMessage, StepProgress } from '@green/ui';

export function ChangeSetReviewPanel({ preview, applying, note, rejectedReasons = [], onApply, onCancel }: { preview: ChangeSetPreview; applying?: boolean; note?: string; rejectedReasons?: string[]; onApply: () => void; onCancel: () => void }) {
  const additions = preview.additions?.length ?? 0;
  const updates = preview.updates?.length ?? 0;
  const deletions = preview.deletion_ids?.length ?? 0;
  const blocked = preview.candidate_results?.filter((item) => item.status === 'blocked') ?? [];
  const rejectionSummary = [...rejectedReasons.reduce((counts, reason) => counts.set(reason, (counts.get(reason) ?? 0) + 1), new Map<string, number>())]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3);
  return <div className="project-inspector change-set-review">
    <header><span><strong>{preview.label}</strong><small>Предпросмотр изменений</small></span></header>
    <section className="change-set-review__summary">
      <StepProgress current={2} steps={[{ id: 'areas', label: 'Участки' }, { id: 'placement', label: 'Посадки' }, { id: 'review', label: 'Проверка' }]} />
      <h3>Проверьте схему</h3>
      <dl>
        {additions ? <><dt>Новых посадок</dt><dd>{additions}</dd></> : null}
        {updates ? <><dt>Изменено</dt><dd>{updates}</dd></> : null}
        {deletions ? <><dt>Удалено</dt><dd>{deletions}</dd></> : null}
      </dl>
      {blocked.length ? <InlineMessage tone="error">{blocked[0].reason}{blocked.length > 1 ? ` Ещё ${blocked.length - 1}` : ''}</InlineMessage> : null}
      {!blocked.length ? <p>Пунктиром показан результат до сохранения</p> : null}
      {note ? <InlineMessage tone="info">{note}</InlineMessage> : null}
      {additions ? <div className="change-set-review__checks"><strong>Учтено при расчёте</strong><span>Рабочая зона, здания, дороги, существующая зелень, вода и технические зоны, шаг посадок, прогноз кроны и корней</span></div> : null}
      {rejectionSummary.length ? <div className="change-set-review__checks"><strong>Исключено при поиске</strong><ul>{rejectionSummary.map(([reason, count]) => <li key={reason}>{count}: {reason}</li>)}</ul></div> : null}
    </section>
    <div className="inspector-spacer" />
    <footer><Button variant="secondary" disabled={applying} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={applying} disabled={!preview.can_apply} onClick={onApply}>{additions ? `Добавить ${additions}` : 'Применить'}</Button></footer>
  </div>;
}
