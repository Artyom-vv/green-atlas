import type { PlanObject, ValidationIssue } from '@green/api-client';
import { Copy, Crosshair, Leaf, Lock, Move, Trash2, Unlock } from 'lucide-react';
import { Button, HelpDisclosure } from '@green/ui';
import { type GrowthHorizon } from './GrowthHorizonControl';
import { EditorActions, EditorPanel } from './EditorPanel';
import { EditorGrowth } from './EditorGrowth';

type GroupInspectorProps = {
  mapMode?: '2d' | '3d';
  objects: PlanObject[];
  issues?: ValidationIssue[];
  disabled?: boolean;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon: (value: GrowthHorizon) => void;
  onSpecies: () => void;
  onCopy: () => void;
  onMove?: () => void;
  onFit?: () => void;
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

export function GroupInspector({ objects, issues, disabled, growthHorizon, onGrowthHorizon, onSpecies, onCopy, onMove, onFit, onLock, onDelete, mapMode = '2d' }: GroupInspectorProps) {
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
  const assigned = objects.filter(object => object.species_revision_id).length;
  return <EditorPanel title="Выделение">
    {onFit ? <EditorActions><Button variant="secondary" controlSize="compact" icon={Crosshair} onClick={onFit}>К выделению</Button></EditorActions> : null}
    <section className="editor-panel__section">
      <h3>Выбрано {objects.length}</h3>
      <div className="editor-panel__summary"><span>Деревья {trees}</span><span>Кустарники {shrubs}</span></div>
      <dl className="editor-panel__metrics"><dt>Виды назначены</dt><dd>{assigned} из {objects.length}</dd><dt>Закреплены</dt><dd>{locked}</dd></dl>
      <EditorActions grid>
        <Button className="editor-action-wide" variant="secondary" controlSize="compact" icon={Leaf} disabled={disabled || locked > 0} onClick={onSpecies}>Назначить виды</Button>
        {onMove ? <Button className={mapMode === '3d' ? 'editor-action-wide' : undefined} variant="secondary" controlSize="compact" icon={Move} disabled={disabled || locked > 0} onClick={onMove}>{mapMode === '3d' ? 'Переместить в 2D' : 'Переместить'}</Button> : null}
        <Button className={mapMode === '3d' ? 'editor-action-wide' : undefined} variant="secondary" controlSize="compact" icon={Copy} disabled={disabled} onClick={onCopy}>{mapMode === '3d' ? 'Копировать в 2D' : 'Копировать'}</Button>
        <Button variant="secondary" controlSize="compact" icon={locked ? Unlock : Lock} disabled={disabled} onClick={() => onLock(!locked)}>{locked ? locked < objects.length ? `Открепить ${locked}` : 'Открепить' : 'Закрепить'}</Button>
        <Button variant="danger" controlSize="compact" icon={Trash2} disabled={disabled || locked > 0} onClick={onDelete}>Удалить</Button>
      </EditorActions>
      {locked > 0 ? <p className="editor-panel__hint">Для изменения посадок снимите закрепление.</p> : null}
    </section>
    <section className="editor-panel__section"><EditorGrowth objects={objects} value={growthHorizon} onChange={onGrowthHorizon} showControl={mapMode === '2d'} /></section>
    {problems.length ? <section className="editor-panel__section" aria-label="Проблемы выбранных объектов">
      <div role="status" aria-label={`Ошибки: ${errorCount}. Замечания: ${warningCount}.`} className="editor-panel__summary">{errorCount ? <span>Ошибки {errorCount}</span> : null}{warningCount ? <span>Замечания {warningCount}</span> : null}</div>
      <HelpDisclosure title={`Что требует внимания (${problems.length})`}><ul className="editor-problem-list">{problems.map(problem => <li key={problem.key}><strong>{problem.title}</strong><small>{problem.description}</small>{problem.meta ? <small>{problem.meta}</small> : null}</li>)}</ul></HelpDisclosure>
    </section> : null}
  </EditorPanel>;
}
