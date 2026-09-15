import type { PlanObject, ValidationIssue } from '@green/api-client';

export interface SelectionProblem {
  key: string;
  severity: ValidationIssue['severity'];
  title: string;
  description: string;
  meta?: string;
}

export function selectionProblems(
  objects: PlanObject[],
  issues?: ValidationIssue[],
): SelectionProblem[] {
  const selectedIds = new Set(
    objects.flatMap((object) => (object.id ? [object.id] : [])),
  );
  if (issues !== undefined)
    return issues
      .filter(
        (issue) =>
          Boolean(issue.object_id && selectedIds.has(issue.object_id)) ||
          issue.related_object_ids?.some((id) => selectedIds.has(id)),
      )
      .map((issue, index) => ({
        key:
          issue.id ??
          `${issue.code}:${issue.object_id ?? 'selection'}:${index}`,
        severity: issue.severity,
        title: issue.title,
        description: `${issue.description}${issue.suggested_action ? ` Действие: ${issue.suggested_action}` : ''}`,
        meta:
          issue.actual !== null && issue.actual !== undefined
            ? `${issue.actual} / ${issue.required ?? '—'}${issue.unit ? ` ${issue.unit}` : ''}`
            : undefined,
      }));
  return objects.flatMap((object, index) =>
    object.status === 'valid'
      ? []
      : [
          {
            key: object.id ?? `${object.kind}:${index}`,
            severity: object.status,
            title:
              object.status === 'error'
                ? 'Ошибка размещения'
                : 'Замечание по посадке',
            description:
              object.status === 'error'
                ? 'Позиция не проходит обязательную геометрическую проверку.'
                : object.species_revision_id
                  ? 'Проверьте замечания по исходным данным, кроне или корневой зоне.'
                  : 'Порода не назначена, поэтому прогноз роста недоступен.',
            meta: object.id,
          },
        ],
  );
}
