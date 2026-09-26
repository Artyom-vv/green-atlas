import { expect, it } from 'vitest';
import type { PlanObject, ValidationIssue } from '@green/api-client';
import { selectionProblems } from './selectionProblems';
import { groupWorkspaceIssues } from '@/entities/validation/model/issueGroups';

it('counts the same causes as workspace checks, retaining different measurements', () => {
  const objects = [
    { id: 'a', kind: 'shrub', status: 'warning' },
  ] as PlanObject[];
  const issues = [
    {
      id: '1',
      code: 'TOO_CLOSE',
      rule_id: 'road',
      object_id: 'a',
      severity: 'warning',
      title: 'Отступ',
      description: 'Дорога',
      actual: 1,
      required: 2,
    },
    {
      id: '2',
      code: 'TOO_CLOSE',
      rule_id: 'road',
      object_id: 'a',
      severity: 'error',
      title: 'Отступ',
      description: 'Дорога',
      actual: 0.5,
      required: 2,
    },
    {
      id: '3',
      code: 'TOO_CLOSE',
      rule_id: 'road',
      object_id: 'a',
      severity: 'error',
      title: 'Отступ',
      description: 'Дорога',
      actual: 0.5,
      required: 2,
    },
    {
      id: '4',
      code: 'UNRELATED',
      object_id: 'b',
      severity: 'warning',
      title: 'Чужое',
      description: 'Чужое',
    },
  ] as ValidationIssue[];
  const result = selectionProblems(objects, issues);
  expect(result).toHaveLength(
    groupWorkspaceIssues(issues.slice(0, 3), objects).length,
  );
  expect(result[0].severity).toBe('error');
  expect(result[0].details).toHaveLength(2);
  expect(result[0].details?.map((item) => item.meta)).toEqual([
    '1 / 2',
    '0.5 / 2',
  ]);
});
