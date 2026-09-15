import type { ChangeSetPreview } from '@green/api-client';

export function changeReviewSummary(
  preview: ChangeSetPreview,
  rejectedReasons: string[],
) {
  const additions = Math.max(
    preview.additions?.length ?? 0,
    preview.candidate_results?.filter((item) => item.type === 'add').length ??
      0,
  );
  const updates = Math.max(
    preview.updates?.length ?? 0,
    preview.candidate_results?.filter((item) => item.type === 'update')
      .length ?? 0,
  );
  const deletions = Math.max(
    preview.deletion_ids?.length ?? 0,
    preview.candidate_results?.filter((item) => item.type === 'delete')
      .length ?? 0,
  );
  const blocked =
    preview.candidate_results?.filter((item) => item.status === 'blocked') ??
    [];
  const uncertain =
    preview.candidate_results?.filter(
      (item) => item.status === 'unknown' || item.status === 'soft_conflict',
    ) ?? [];
  const problems = [
    ...[...blocked, ...uncertain]
      .reduce((groups, item) => {
        const key = `${item.status}:${item.rule_id ?? item.code}`;
        const current = groups.get(key) ?? {
          reason: [item.reason, item.suggested_action]
            .filter(Boolean)
            .join(' '),
          count: 0,
          details: [] as string[],
        };
        current.count++;
        if (!current.details.includes(item.reason))
          current.details.push(item.reason);
        groups.set(key, current);
        return groups;
      }, new Map<string, { reason: string; count: number; details: string[] }>())
      .values(),
  ];
  const rejected = [
    ...rejectedReasons.reduce(
      (counts, reason) => counts.set(reason, (counts.get(reason) ?? 0) + 1),
      new Map<string, number>(),
    ),
  ].sort((a, b) => b[1] - a[1]);
  return {
    additions,
    updates,
    deletions,
    blocked,
    uncertain,
    problems,
    rejected,
  };
}

export type ChangeReviewSummary = ReturnType<typeof changeReviewSummary>;
