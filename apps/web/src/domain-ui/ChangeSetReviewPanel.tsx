import type { ChangeSetPreview } from '@green/api-client';
import { Button, Dialog } from '@green/ui';
import { EditorDisclosure } from './EditorPanel';
import './change-review.css';

export function ChangeSetReviewPanel({ preview, open = true, applying, error, note, rejectedReasons = [], unverifiedData = [], onApply, onCancel, onInspect }: { preview: ChangeSetPreview; open?: boolean; applying?: boolean; error?: string; note?: string; rejectedReasons?: string[]; unverifiedData?: string[]; onApply: () => void; onCancel: () => void; onInspect?: () => void }) {
  const additions = Math.max(preview.additions?.length ?? 0, preview.candidate_results?.filter(item => item.type === 'add').length ?? 0);
  const updates = Math.max(preview.updates?.length ?? 0, preview.candidate_results?.filter(item => item.type === 'update').length ?? 0);
  const deletions = Math.max(preview.deletion_ids?.length ?? 0, preview.candidate_results?.filter(item => item.type === 'delete').length ?? 0);
  const blocked = preview.candidate_results?.filter(item => item.status === 'blocked') ?? [];
  const uncertain = preview.candidate_results?.filter(item => item.status === 'unknown' || item.status === 'soft_conflict') ?? [];
  const problems = [...[...blocked, ...uncertain].reduce((groups, item) => {
    const key = `${item.status}:${item.rule_id ?? item.code}`;
    const current = groups.get(key) ?? { reason: [item.reason, item.suggested_action].filter(Boolean).join(' '), count: 0, details: [] as string[] };
    current.count++;
    if (!current.details.includes(item.reason)) current.details.push(item.reason);
    groups.set(key, current); return groups;
  }, new Map<string, { reason: string; count: number; details: string[] }>()).values()];
  const rejected = [...rejectedReasons.reduce((counts, reason) => counts.set(reason, (counts.get(reason) ?? 0) + 1), new Map<string, number>())].sort((a, b) => b[1] - a[1]);
  const close = () => { if (!applying) (onInspect ?? onCancel)(); };
  return <Dialog open={open} title={blocked.length ? 'Изменение недоступно' : !preview.can_apply ? 'Нужна проверка' : 'Применить изменения?'} onClose={close} footer={<>
    <Button variant="secondary" disabled={applying} onClick={onCancel}>Отменить изменение</Button>
    {onInspect ? <Button variant="secondary" disabled={applying} onClick={onInspect}>Посмотреть на карте</Button> : null}
    {preview.can_apply ? <Button variant="primary" loading={applying} onClick={onApply}>Применить</Button> : null}
  </>}>
    <div className="change-review">
      <p className="change-review__label">{preview.label}</p>
      <dl className="change-review__metrics">
        {additions ? <><dt>Добавить</dt><dd>{additions}</dd></> : null}
        {updates ? <><dt>Изменить</dt><dd>{updates}</dd></> : null}
        {deletions ? <><dt>Удалить</dt><dd>{deletions}</dd></> : null}
      </dl>
      {problems.length ? <section aria-label="Причины ограничения"><p className="change-review__problem-count">Не проходят проверку: {blocked.length + uncertain.length}</p><ul>{problems.slice(0, 2).map(item => <li key={item.reason}>{item.reason}{item.count > 1 ? <small>Позиций: {item.count}</small> : null}</li>)}</ul>
        {problems.length > 2 || problems.some(item => item.details.length > 1) ? <EditorDisclosure title="Все причины"><ul>{problems.map(item => <li key={item.reason}><strong>Позиций: {item.count}</strong>{item.details.map(reason => <p key={reason}>{reason}</p>)}</li>)}</ul></EditorDisclosure> : null}
      </section> : null}
      {!preview.can_apply && !problems.length ? <p>Вариант не прошёл проверку. Вернитесь к параметрам или отмените изменение.</p> : null}
      {note ? <p>{note}</p> : null}
      {unverifiedData.length ? <EditorDisclosure title="Ограничения исходных данных"><ul>{unverifiedData.map(item => <li key={item}>{item}</li>)}</ul></EditorDisclosure> : null}
      {rejected.length ? <EditorDisclosure title="Исключено при поиске"><ul>{rejected.map(([reason, count]) => <li key={reason}>{count}: {reason}</li>)}</ul></EditorDisclosure> : null}
      {error ? <p className="change-review__error" role="alert">{error}</p> : null}
      {preview.can_apply ? <p className="change-review__note">До подтверждения сохранённый план не меняется.</p> : null}
    </div>
  </Dialog>;
}
