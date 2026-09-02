import type { PlanObject, ValidationIssue } from '@green/api-client';
import { Copy, Leaf, Lock, Trash2, Unlock } from 'lucide-react';
import { Button, HelpDisclosure } from '@green/ui';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';

type GroupInspectorProps = {
  objects: PlanObject[];
  issues?: ValidationIssue[];
  disabled?: boolean;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon: (value: GrowthHorizon) => void;
  onSpecies: () => void;
  onCopy: () => void;
  onLock: (locked: boolean) => void;
  onDelete: () => void;
};

type SelectionProblem = {
  key: string;
  severity: 'error' | 'warning';
  title: string;
  description: string;
  meta?: string;
};

const issueMeta = (issue: ValidationIssue) => issue.actual !== null && issue.actual !== undefined
  ? `${issue.actual} / ${issue.required ?? '—'}${issue.unit ? ` ${issue.unit}` : ''}`
  : undefined;

export function GroupInspector({ objects, issues, disabled, growthHorizon, onGrowthHorizon, onSpecies, onCopy, onLock, onDelete }: GroupInspectorProps) {
  const locked = objects.filter((object) => object.locked).length;
  const trees = objects.filter((object) => object.kind === 'tree').length;
  const shrubs = objects.length - trees;
  const selectedIds = new Set(objects.flatMap((object) => object.id ? [object.id] : []));
  const problems: SelectionProblem[] = issues !== undefined
    ? issues.filter((issue) => (issue.object_id ? selectedIds.has(issue.object_id) : false) || issue.related_object_ids?.some((id) => selectedIds.has(id))).map((issue, index) => ({
      key: issue.id ?? `${issue.code}:${issue.object_id ?? 'selection'}:${index}`,
      severity: issue.severity,
      title: issue.title,
      description: `${issue.description}${issue.suggested_action ? ` Действие: ${issue.suggested_action}` : ''}`,
      meta: issueMeta(issue),
    }))
    : objects.flatMap((object, index) => object.status === 'valid' ? [] : [{
      key: object.id ?? `${object.kind}:${index}`,
      severity: object.status,
      title: object.status === 'error' ? 'Ошибка размещения' : 'Замечание по посадке',
      description: object.status === 'error'
        ? 'Позиция не проходит обязательную геометрическую проверку.'
        : object.species_revision_id
          ? 'Проверьте замечания по исходным данным, кроне или корневой зоне.'
          : 'Порода не назначена, поэтому прогноз роста недоступен.',
      meta: object.id,
    } satisfies SelectionProblem]);
  const errorCount = problems.filter((problem) => problem.severity === 'error').length;
  const warningCount = problems.length - errorCount;
  return <div className="project-inspector multi-selection-inspector">
    <header><span><strong>Выбрано посадок</strong><small>{objects.length} объектов</small></span></header>
    <section className="group-selection-summary">
      <h3>Состав группы</h3>
      <dl><dt>Деревья</dt><dd>{trees}</dd><dt>Кустарники</dt><dd>{shrubs}</dd>{locked ? <><dt>Закреплено</dt><dd>{locked}</dd></> : null}</dl>
    </section>
    {problems.length ? <section className="group-selection-validation" aria-label="Проблемы выбранных объектов">
      <div className="group-selection-validation__counts" role="status" aria-label={`Ошибки: ${errorCount}. Замечания: ${warningCount}.`}>
        {errorCount ? <span className="is-error"><i />Ошибки <b>{errorCount}</b></span> : null}
        {warningCount ? <span className="is-warning"><i />Замечания <b>{warningCount}</b></span> : null}
      </div>
      <HelpDisclosure title={`Что требует внимания (${problems.length})`}>
        <ul className="group-selection-validation__list">
          {problems.map((problem) => <li key={problem.key} className={`is-${problem.severity}`}>
            <i aria-hidden="true" />
            <span><strong>{problem.title}</strong><small>{problem.description}</small></span>
            {problem.meta ? <code title={problem.meta}>{problem.meta}</code> : null}
          </li>)}
        </ul>
        <p>Полные причины и поиск объектов на карте — во вкладке «Проверка».</p>
      </HelpDisclosure>
    </section> : null}
    <section className="group-selection-actions">
      <Button variant="secondary" icon={Leaf} disabled={disabled || locked > 0} onClick={onSpecies}>Назначить породу</Button>
      <Button variant="secondary" icon={Copy} disabled={disabled} onClick={onCopy}>Копировать</Button>
      <Button variant="secondary" icon={locked === objects.length ? Unlock : Lock} disabled={disabled} onClick={() => onLock(locked !== objects.length)}>{locked === objects.length ? 'Открепить' : 'Закрепить'}</Button>
      <Button className="group-selection-actions__delete" variant="danger" icon={Trash2} disabled={disabled || locked > 0} onClick={onDelete}>Удалить выбранные</Button>
    </section>
    {objects.some((object) => object.canopy_forecast?.length) ? <GrowthHorizonControl value={growthHorizon} forecasts={objects} onChange={onGrowthHorizon} /> : null}
  </div>;
}
