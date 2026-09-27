import type { PlanObject, ValidationIssue } from '@green/api-client';
import { groupWorkspaceIssues } from '@/entities/validation/model/issueGroups';

export interface SelectionProblem {
  key: string;
  severity: ValidationIssue['severity'];
  title: string;
  description: string;
  meta?: string;
  details?: { description: string; meta?: string }[];
}

export function selectionProblems(
  objects: PlanObject[],
  issues?: ValidationIssue[],
): SelectionProblem[] {
  const selectedIds = new Set(
    objects.flatMap((object) => (object.id ? [object.id] : [])),
  );
  if (issues !== undefined)
    return groupWorkspaceIssues(
      issues.filter(
        (issue) =>
          Boolean(issue.object_id && selectedIds.has(issue.object_id)) ||
          issue.related_object_ids?.some((id) => selectedIds.has(id)),
      ),
      objects,
    ).map((group) => {
      const details = [
        ...new Map(
          group.items.map((issue) => {
            const detail = {
              description: `${issue.description}${issue.suggested_action ? ` Действие: ${issue.suggested_action}` : ''}`,
              meta:
                issue.actual !== null && issue.actual !== undefined
                  ? `${issue.actual} / ${issue.required ?? '—'}${issue.unit ? ` ${issue.unit}` : ''}`
                  : undefined,
            };
            return [JSON.stringify(detail), detail];
          }),
        ).values(),
      ];
      return {
        key: group.key,
        severity: group.hasError ? 'error' : 'warning',
        title: group.title,
        description: details[0].description,
        meta:
          details.length === 1
            ? details[0].meta
            : `Различных проверок: ${details.length}`,
        details: details.length > 1 ? details : undefined,
      };
    });
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
