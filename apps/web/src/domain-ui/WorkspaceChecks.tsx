import type { PlanObject, ValidationIssue } from '@green/api-client';
import { Button } from '@green/ui';
import { ChevronRight } from 'lucide-react';

export function WorkspaceChecks({ disabled, issues, objects = [], onLocate, onAssign }: { disabled?: boolean; issues: ValidationIssue[]; objects?: PlanObject[]; onLocate: (ids: string[]) => void; onAssign: (ids: string[]) => void }) {
  const byId = new Map(objects.map(object => [object.id, object]));
  const issueIds = (issue: ValidationIssue) => [...new Set([...(issue.object_id ? [issue.object_id] : []), ...(issue.related_object_ids ?? [])])];
  const groups = [...issues.reduce((result, issue) => {
    const object = byId.get(issue.object_id ?? undefined);
    const key = issue.code === 'SPECIES_UNASSIGNED' ? `${issue.code}:${object?.kind ?? 'unknown'}:${Boolean(object?.locked)}` : issue.rule_id ?? issue.code;
    result.set(key, [...(result.get(key) ?? []), issue]); return result;
  }, new Map<string, ValidationIssue[]>())].sort(([, left], [, right]) => {
    const priority = (items: ValidationIssue[]) => items.some(item => item.severity === 'error') ? 0 : items[0].code === 'SPECIES_UNASSIGNED' ? 2 : 1;
    return priority(left) - priority(right);
  });
  return <section className="workspace-checks" aria-label="Проверка проекта">{!groups.length ? <p>По загруженным данным нарушений не обнаружено.</p> : groups.map(([key, items]) => {
    const ids = [...new Set(items.flatMap(issueIds))];
    const object = byId.get(items[0].object_id ?? undefined);
    const missing = items[0].code === 'SPECIES_UNASSIGNED';
    const title = missing && object ? object.kind === 'tree' ? 'Деревья без породы' : 'Кустарники без вида' : items[0].title;
    return <div className="workspace-check-group" key={key}>
      <div className="workspace-check-row">
        <div><strong>{title}</strong><span>{missing ? object?.locked ? 'Закреплены. Снимите закрепление перед назначением.' : 'Данные о посадках' : items.some(item => item.severity === 'error') ? 'Ошибка размещения' : 'Требует проверки'}</span><span>{ids.length ? `${ids.length} шт.` : `${items.length} замечаний`}</span></div>
        <div className="workspace-check-actions">{ids.length ? <Button variant="ghost" controlSize="compact" onClick={() => onLocate(ids)}>Показать</Button> : null}{missing && ids.length && !object?.locked ? <Button variant="secondary" controlSize="compact" disabled={disabled} onClick={() => onAssign(ids)}>Назначить виды</Button> : null}</div>
      </div>
      {!missing ? <details><summary><ChevronRight size={13} />Подробности проверки</summary>{items.map((item, index) => <div className="workspace-check-detail" key={item.id ?? index}><p>{item.description}</p>{item.actual != null && item.required != null ? <p>Фактически: {item.actual} {item.unit}. Требуется: {item.required} {item.unit}.</p> : null}{issueIds(item).length ? <Button variant="ghost" controlSize="compact" onClick={() => onLocate(issueIds(item))}>Показать объект</Button> : null}</div>)}</details> : null}
    </div>;
  })}</section>;
}
