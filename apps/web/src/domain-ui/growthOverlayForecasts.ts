import type { GrowthEnvelopeForecast, PlanObject } from '@green/api-client';
import { forecastAt } from './growthForecast';

export type GrowthOverlayForecast = {
  object: PlanObject;
  selected: boolean;
  canopy?: GrowthEnvelopeForecast;
  roots?: GrowthEnvelopeForecast;
};

/**
 * Resolve every growth envelope from the same bounded forecast source used by
 * the inspector. Preview objects replace durable objects with the same ID so
 * the map never renders two contradictory envelopes for one planting.
 */
export function growthOverlayForecasts(
  objects: readonly PlanObject[],
  selectedIds: readonly string[],
  year: number | undefined,
  previewObjects: readonly PlanObject[] = [],
): GrowthOverlayForecast[] {
  if (year === undefined) return [];
  const selected = new Set(selectedIds);
  const candidates = new globalThis.Map<string, PlanObject>();
  const wholePlan = selected.size === 0 && year > 0;
  for (const object of objects) {
    if (object.id && (wholePlan || selected.has(object.id))) candidates.set(object.id, object);
  }
  for (const object of previewObjects) {
    if (object.id) candidates.set(object.id, object);
  }
  return [...candidates.values()].flatMap((object) => {
    if (!object.id) return [];
    const canopy = forecastAt(object.canopy_forecast, year);
    const roots = forecastAt(object.root_forecast, year);
    return canopy || roots ? [{ object, selected: selected.has(object.id), canopy, roots }] : [];
  });
}
