import { expect, it } from 'vitest';
import { metadataOnlyObjectIds } from './planPresentation';
it('never masks another warning or error with missing-species presentation', () => {
  const issue = { severity: 'warning' as const, title: '', description: '', code: 'SPECIES_UNASSIGNED' };
  expect(metadataOnlyObjectIds([
    { ...issue, object_id: 'a' }, { ...issue, object_id: 'b' },
    { ...issue, object_id: 'b', code: 'GROWTH_BUILDING' },
    { ...issue, object_id: 'c', code: 'POSITION_BLOCKED', severity: 'error' },
  ])).toEqual(['a']);
});
