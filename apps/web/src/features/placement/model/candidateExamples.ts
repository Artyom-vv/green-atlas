import type { PatternPreview } from '@green/api-client';

type Candidate = NonNullable<PatternPreview['skipped']>[number];
export const INSPECTION_EXAMPLE_LIMIT = 24;
export const INSPECTION_EXAMPLES_PER_CAUSE = 3;

/** Representative map locations, not the planner's internal search log. */
export function candidateExamples(skipped: Candidate[], spacing: number) {
  const groups = new Map<string, { candidate: Candidate; index: number }[]>();
  skipped.forEach((candidate, index) => {
    if (candidate.category === 'spacing') return;
    const key = `${candidate.code}:${candidate.source_layer ?? ''}`;
    const group = groups.get(key) ?? [];
    group.push({ candidate, index });
    groups.set(key, group);
  });
  const selected: { candidate: Candidate; index: number }[][] = [];
  for (const group of groups.values()) {
    const representatives = [group[0]];
    let remaining = group.slice(1);
    while (
      remaining.length &&
      representatives.length < INSPECTION_EXAMPLES_PER_CAUSE
    ) {
      const ranked = remaining
        .map((value) => ({
          value,
          distance: Math.min(
            ...representatives.map((other) =>
              Math.hypot(
                value.candidate.x - other.candidate.x,
                value.candidate.y - other.candidate.y,
              ),
            ),
          ),
        }))
        .sort((a, b) => b.distance - a.distance);
      if (ranked[0].distance < spacing || ranked[0].distance === 0) break;
      representatives.push(ranked[0].value);
      remaining = remaining.filter((value) => value !== ranked[0].value);
    }
    selected.push(representatives);
  }
  // One example of each cause before second/third examples of frequent causes.
  return Array.from({ length: INSPECTION_EXAMPLES_PER_CAUSE }, (_, row) =>
    selected.flatMap((group) => (group[row] ? [group[row]] : [])),
  )
    .flat()
    .slice(0, INSPECTION_EXAMPLE_LIMIT);
}
