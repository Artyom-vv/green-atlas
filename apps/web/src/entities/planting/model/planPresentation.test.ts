import { expect, it } from 'vitest';
import { metadataOnlyObjectIds } from '@/entities/planting/model/planPresentation';
it('never masks another warning or error with missing-species presentation', () => {
  const issue = {
    severity: 'warning' as const,
    title: '',
    description: '',
    code: 'SPECIES_UNASSIGNED',
  };
  expect(
    metadataOnlyObjectIds([
      { ...issue, object_id: 'a' },
      { ...issue, object_id: 'b' },
      { ...issue, object_id: 'b', code: 'GROWTH_BUILDING' },
      { ...issue, object_id: 'c', code: 'POSITION_BLOCKED', severity: 'error' },
    ]),
  ).toEqual(['a']);
});

it('keeps the shared missing-network warning visible on every planting', () => {
  expect(
    metadataOnlyObjectIds([
      {
        code: 'SPECIES_UNASSIGNED',
        object_id: 'a',
        severity: 'warning',
        title: '',
        description: '',
      },
      {
        code: 'NO_NETWORK_FEATURES',
        object_id: null,
        severity: 'warning',
        title: '',
        description: '',
      },
    ]),
  ).toEqual([]);
});

it.each(['UNRELATED_PROJECT_NOTE', 'NETWORK_CROWN_CLEARANCE_UNRESOLVED'])(
  'does not treat %s as a missing-source warning for all objects',
  (code) => {
    expect(
      metadataOnlyObjectIds([
        {
          code: 'SPECIES_UNASSIGNED',
          object_id: 'a',
          severity: 'warning',
          title: '',
          description: '',
        },
        {
          code,
          object_id: null,
          severity: 'warning',
          title: '',
          description: '',
        },
      ]),
    ).toEqual(['a']);
  },
);
