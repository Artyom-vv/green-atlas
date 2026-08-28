import type { ChangeSetPreview } from '@green/api-client';
import { Button, InlineMessage } from '@green/ui';

export function ChangeSetReviewPanel({ preview, applying, note, onApply, onCancel }: { preview: ChangeSetPreview; applying?: boolean; note?: string; onApply: () => void; onCancel: () => void }) {
  const additions = preview.additions?.length ?? 0;
  const updates = preview.updates?.length ?? 0;
  const deletions = preview.deletion_ids?.length ?? 0;
  const blocked = preview.candidate_results?.filter((item) => item.status === 'blocked') ?? [];
  return <div className="project-inspector change-set-review">
    <header><span><strong>{preview.label}</strong><small>Предпросмотр изменений</small></span></header>
    <section className="change-set-review__summary">
      <h3>Что изменится</h3>
      <dl>
        {additions ? <><dt>Новых посадок</dt><dd>{additions}</dd></> : null}
        {updates ? <><dt>Изменено</dt><dd>{updates}</dd></> : null}
        {deletions ? <><dt>Удалено</dt><dd>{deletions}</dd></> : null}
      </dl>
      {blocked.length ? <InlineMessage tone="error">{blocked[0].reason}{blocked.length > 1 ? ` Ещё: ${blocked.length - 1}.` : ''}</InlineMessage> : null}
      {!blocked.length ? <p>Пунктир на карте показывает результат до сохранения.</p> : null}
      {note ? <InlineMessage tone="info">{note}</InlineMessage> : null}
    </section>
    <div className="inspector-spacer" />
    <footer><Button variant="secondary" disabled={applying} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={applying} disabled={!preview.can_apply} onClick={onApply}>Применить</Button></footer>
  </div>;
}
