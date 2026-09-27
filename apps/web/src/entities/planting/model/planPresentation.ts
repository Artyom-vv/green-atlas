import type { ValidationIssue } from '@green/api-client';

/** Missing species is incomplete metadata, not a spatial collision. This
 * changes presentation only: the release checks and persisted status remain. */
export function metadataOnlyObjectIds(
  issues: readonly ValidationIssue[],
): string[] {
  if (
    issues.some(
      (issue) => issue.code === 'NO_NETWORK_FEATURES' && !issue.object_id,
    )
  ) {
    return [];
  }
  const missing = new Set<string>();
  const spatial = new Set<string>();
  for (const issue of issues) {
    if (!issue.object_id) continue;
    if (issue.code === 'SPECIES_UNASSIGNED') missing.add(issue.object_id);
    else spatial.add(issue.object_id);
  }
  return [...missing].filter((id) => !spatial.has(id));
}
