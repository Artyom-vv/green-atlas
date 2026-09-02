import { describe, expect, it } from 'vitest';
import type { PlanObject } from '@green/api-client';
import { groupTransformDraft } from './groupTransform';

const objects = [
  { id: 'one', kind: 'tree', x: 10, y: 10, radius: 1.5, layout_radius_m: 2, group_ids: ['old'], locked: false },
  { id: 'two', kind: 'tree', x: 20, y: 10, radius: 1.5, layout_radius_m: 2, group_ids: ['old'], locked: false },
] as PlanObject[];

describe('groupTransformDraft', () => {
  it('moves the group centre without changing its internal layout', () => {
    const draft = groupTransformDraft(4, objects, 'move', [30, 25]);

    expect(draft).toMatchObject({ base_plan_version: 4, label: 'Перемещение группы (2)', policy: 'all_or_nothing' });
    expect(draft?.operations).toEqual([
      { type: 'update', object_id: 'one', changes: { x: 25, y: 25 } },
      { type: 'update', object_id: 'two', changes: { x: 35, y: 25 } },
    ]);
  });

  it('does not create a revision when a selected group was clicked without moving', () => {
    expect(groupTransformDraft(4, objects, 'move', [15, 10])).toBeUndefined();
  });

  it('copies objects into a distinct group while preserving planting metadata', () => {
    const draft = groupTransformDraft(4, objects, 'copy', [30, 25], 'copy-group');

    expect(draft?.operations).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'add', object: expect.objectContaining({ x: 25, y: 25, layout_radius_m: 2, group_ids: ['copy-group'], locked: false }) }),
      expect.objectContaining({ type: 'add', object: expect.objectContaining({ x: 35, y: 25, layout_radius_m: 2, group_ids: ['copy-group'], locked: false }) }),
    ]));
  });
});
