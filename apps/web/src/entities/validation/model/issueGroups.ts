import type { PlanObject, ValidationIssue } from '@green/api-client';

export interface WorkspaceIssueGroup {
  key: string;
  items: ValidationIssue[];
  ids: string[];
  title: string;
  missing: boolean;
  locked: boolean;
  hasError: boolean;
}

export function issueObjectIds(issue: ValidationIssue): string[] {
  return [
    ...new Set([
      ...(issue.object_id ? [issue.object_id] : []),
      ...(issue.related_object_ids ?? []),
    ]),
  ];
}

export function groupIssuesByRule(issues: ValidationIssue[]) {
  const groups = new Map<string, ValidationIssue[]>();
  for (const issue of issues) {
    const key = issue.rule_id ?? issue.code;
    groups.set(key, [...(groups.get(key) ?? []), issue]);
  }
  return [...groups];
}

const priority = (issues: ValidationIssue[]) =>
  issues.some((issue) => issue.severity === 'error')
    ? 0
    : issues[0].code === 'SPECIES_UNASSIGNED'
      ? 2
      : 1;

/** Assignment groups share kind and lock state; rule details retain individual evidence. */
export function groupWorkspaceIssues(
  issues: ValidationIssue[],
  objects: PlanObject[],
): WorkspaceIssueGroup[] {
  const byId = new Map(objects.map((object) => [object.id, object]));
  const groups = new Map<string, ValidationIssue[]>();
  for (const issue of issues) {
    const object = byId.get(issue.object_id ?? undefined);
    const key =
      issue.code === 'SPECIES_UNASSIGNED'
        ? `${issue.code}:${object?.kind ?? 'unknown'}:${Boolean(object?.locked)}`
        : (issue.rule_id ?? issue.code);
    groups.set(key, [...(groups.get(key) ?? []), issue]);
  }
  return [...groups]
    .sort(([, left], [, right]) => priority(left) - priority(right))
    .map(([key, items]) => {
      const object = byId.get(items[0].object_id ?? undefined);
      const missing = items[0].code === 'SPECIES_UNASSIGNED';
      return {
        key,
        items,
        missing,
        ids: [...new Set(items.flatMap(issueObjectIds))],
        title:
          missing && object
            ? object.kind === 'tree'
              ? 'Деревья без породы'
              : 'Кустарники без вида'
            : items[0].title,
        locked: Boolean(object?.locked),
        hasError: items.some((issue) => issue.severity === 'error'),
      };
    });
}
