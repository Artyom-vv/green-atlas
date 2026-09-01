import type { ValidationIssue } from '@green/api-client';
import { AlertTriangle, LocateFixed } from 'lucide-react';
import { Button, EmptyState, Icon, ListRow, StatusIndicator } from '@green/ui';

export function ValidationPanel({ issues, onLocate }: { issues: ValidationIssue[]; onLocate: (objectId?: string) => void }) {
  const errors = issues.filter((item) => item.severity === 'error').length;
  const warnings = issues.filter((item) => item.severity === 'warning').length;
  const groups = [...issues.reduce((result, issue) => {
    const key = issue.rule_id ?? issue.code;
    const current = result.get(key) ?? [];
    current.push(issue);
    result.set(key, current);
    return result;
  }, new Map<string, ValidationIssue[]>())];
  return (
    <section className="validation-panel">
      <div className="validation-summary"><StatusIndicator tone={errors ? 'error' : 'success'} label="Ошибки" value={errors} /><StatusIndicator tone={warnings ? 'warning' : 'neutral'} label="Риски" value={warnings} /></div>
      {issues.length === 0 ? <EmptyState title="Нарушений нет" /> : <div className="issue-list">{groups.map(([basis, items]) => <section className="issue-group" key={basis} aria-label={`Основание ${basis}`}>
        <header><span><strong>{basis}</strong><small>{items[0].title}</small></span><b>{items.length}</b></header>
        {items.map((issue) => <ListRow key={issue.id} leading={<span className={`issue-icon issue-icon--${issue.severity}`}><Icon icon={AlertTriangle} /></span>} title={issue.object_id ? 'Посадка требует действия' : issue.title} description={`${issue.description}${issue.suggested_action ? ` Действие: ${issue.suggested_action}` : ''}`} meta={issue.actual !== null && issue.actual !== undefined ? `${issue.actual} / ${issue.required} ${issue.unit ?? ''}` : undefined} actions={issue.object_id ? <Button variant="ghost" icon={LocateFixed} onClick={() => onLocate(issue.object_id ?? undefined)}>Показать</Button> : undefined} />)}
      </section>)}</div>}
    </section>
  );
}
