import { describe, expect, it } from 'vitest';
import type { ChangeSetPreview } from '@green/api-client';
import { invalidateMovePreviewGeneration, isCurrentMovePreviewGeneration, moveValidationFromPreview } from './moveLiveValidation';

const preview = {
  id: 'preview-1',
  digest: 'digest',
  base_plan_version: 1,
  source: 'group',
  label: 'Перемещение',
  can_apply: false,
  updates: [],
  candidate_results: [
    { operation_index: 0, type: 'update', status: 'allowed', code: 'OK', category: 'accepted', reason: 'Допустимо', object_id: 'a' },
    { operation_index: 1, type: 'update', status: 'blocked', code: 'CLEARANCE', category: 'constraint', reason: 'Недостаточный отступ', object_id: 'b' },
  ],
  expires_at: '2030-01-01T00:00:00Z',
} satisfies ChangeSetPreview;

describe('live move validation', () => {
  it('keeps per-object statuses while exposing the worst aggregate result', () => {
    expect(moveValidationFromPreview(preview)).toEqual({
      status: 'blocked',
      reason: 'Недостаточный отступ',
      objectStatuses: { a: 'allowed', b: 'blocked' },
    });
  });

  it('invalidates an in-flight response immediately', () => {
    const generation = { current: 4 };
    const staleRequest = generation.current;

    expect(invalidateMovePreviewGeneration(generation)).toBe(5);
    expect(isCurrentMovePreviewGeneration(generation, staleRequest)).toBe(false);
    expect(isCurrentMovePreviewGeneration(generation, 5)).toBe(true);
  });

  it('does not mark a result without candidate evidence as allowed', () => {
    expect(moveValidationFromPreview({ ...preview, candidate_results: [] })).toMatchObject({
      status: 'unknown',
      reason: 'Положение требует проверки',
    });
  });
});
