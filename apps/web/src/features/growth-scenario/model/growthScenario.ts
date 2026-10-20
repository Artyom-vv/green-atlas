/** Сценарий роста: временные замечания и подписи, независимые от сохранённого плана */
import type {
  GrowthForecastResponse,
  PlanObject,
  ValidationIssue,
} from '@green/api-client';
import { isGeneralDataNotice } from '@/entities/validation/model/issueGroups';
import { countLabel } from '@/shared/format/countLabel';

export function isGrowthIssue(issue: ValidationIssue) {
  return (
    issue.code === 'PLANT_SPACING' ||
    /^(GROWTH_|ROOT_|CANOPY_|CROWN_)/.test(issue.code)
  );
}

export function currentGrowthIssues(
  saved: ValidationIssue[],
  current?: ValidationIssue[],
) {
  if (!current) return saved;
  return Array.from(
    new Map(
      [...saved.filter((issue) => !isGrowthIssue(issue)), ...current].map(
        (issue) => [issue.id ?? `${issue.code}:${issue.object_id}`, issue],
      ),
    ).values(),
  );
}

export function objectsWithIssues(
  objects: PlanObject[],
  issues: ValidationIssue[],
) {
  const statuses = new Map<string, PlanObject['status']>();
  for (const issue of issues) {
    if (issue.severity !== 'error' && isGeneralDataNotice(issue.code)) continue;
    for (const id of new Set([
      ...(issue.related_object_ids ?? []),
      ...(issue.object_id ? [issue.object_id] : []),
    ])) {
      if (statuses.get(id) !== 'error')
        statuses.set(id, issue.severity === 'error' ? 'error' : 'warning');
    }
  }
  return objects.map((object) => ({
    ...object,
    status: object.id ? (statuses.get(object.id) ?? 'valid') : 'valid',
  }));
}

export function growthScenarioSummary(forecast: GrowthForecastResponse) {
  if (forecast.forecast_unavailable_object_ids?.length)
    return `Нет прогноза для ${countLabel(forecast.forecast_unavailable_object_ids.length, 'посадки', 'посадок', 'посадок')}`;
  if (forecast.unverified_object_ids?.length)
    return 'Есть непроверенные посадки';
  const first = forecast.earliest_conflict_year;
  if (first === 0) return 'Есть пересечения сейчас';
  if (first === 1) return 'Пересечения появляются через год';
  const through = forecast.conflict_free_through_year;
  return through == null
    ? 'Прогноз недоступен'
    : `Без пересечений до ${countLabel(through, 'года', 'лет', 'лет')}`;
}
