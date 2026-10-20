/** Проверки временных замечаний: просмотр не меняет сохранённые данные */
import { expect, it } from 'vitest';
import type {
  GrowthForecastResponse,
  PlanObject,
  ValidationIssue,
} from '@green/api-client';
import {
  currentGrowthIssues,
  growthScenarioSummary,
  objectsWithIssues,
} from './growthScenario';

const spacing = {
  id: 'pair',
  code: 'PLANT_SPACING',
  severity: 'error',
  rule_id: 'plant_spacing',
  related_object_ids: ['a', 'b'],
} as ValidationIssue;
const obstacle = {
  id: 'wall',
  code: 'BUILDING_SETBACK',
  severity: 'error',
  object_id: 'a',
} as ValidationIssue;
const object = (id: string) =>
  ({ id, status: 'error', kind: 'tree', x: 0, y: 0, radius: 1 }) as PlanObject;
it('replaces legacy future spacing with current results and keeps physical source findings', () => {
  const saved = [spacing, obstacle];
  const result = currentGrowthIssues(saved, []);
  expect(result).toEqual([obstacle]);
  expect(saved).toEqual([spacing, obstacle]);
  expect(currentGrowthIssues(saved)).toBe(saved);
});
it('marks both neighbours in a temporary view and restores their current statuses', () => {
  const objects = [object('a'), object('b')];
  expect(
    objectsWithIssues(objects, [spacing]).map((item) => item.status),
  ).toEqual(['error', 'error']);
  expect(
    objectsWithIssues(objects, [obstacle]).map((item) => item.status),
  ).toEqual(['error', 'valid']);
  expect(objects.map((item) => item.status)).toEqual(['error', 'error']);
});
it('keeps addressed source uncertainties and serious errors even when their code is usually informational', () => {
  const unknown = {
    ...obstacle,
    id: 'unknown',
    code: 'NATIVE_LOCAL_UNKNOWN',
    rule_id: 'growth_forecast',
  };
  const error = { ...obstacle, id: 'source', code: 'SOURCE_GEOMETRY_PARTIAL' };
  expect(currentGrowthIssues([unknown, error], [])).toEqual([unknown, error]);
  expect(objectsWithIssues([object('a')], [error])[0]?.status).toBe('error');
});
it('reports the computed conflict-free interval and never promises it for missing forecasts', () => {
  const base = {
    year: 5,
    earliest_conflict_year: 6,
    conflict_free_through_year: 5,
    forecast_unavailable_object_ids: [],
  } as unknown as GrowthForecastResponse;
  expect(growthScenarioSummary(base)).toBe('Без пересечений до 5 лет');
  expect(
    growthScenarioSummary({
      ...base,
      earliest_conflict_year: 0,
      conflict_free_through_year: null,
    }),
  ).toBe('Есть пересечения сейчас');
  expect(
    growthScenarioSummary({ ...base, forecast_unavailable_object_ids: ['a'] }),
  ).toBe('Нет прогноза для 1 посадки');
});
