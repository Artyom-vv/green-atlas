import type { GrowthEnvelopeForecast } from '@green/api-client';

export function forecastAt(items: GrowthEnvelopeForecast[] | undefined, year: number): GrowthEnvelopeForecast | undefined {
  if (!items?.length) return undefined;
  const sorted = [...items].sort((a, b) => a.horizon_year - b.horizon_year);
  const lower = [...sorted].reverse().find((item) => item.horizon_year <= year) ?? sorted[0];
  const upper = sorted.find((item) => item.horizon_year >= year) ?? sorted.at(-1)!;
  if (lower.horizon_year === upper.horizon_year) return lower;
  const ratio = (year - lower.horizon_year) / (upper.horizon_year - lower.horizon_year);
  return { ...lower, horizon_year: year, radius_min_m: lower.radius_min_m + (upper.radius_min_m - lower.radius_min_m) * ratio, radius_max_m: lower.radius_max_m + (upper.radius_max_m - lower.radius_max_m) * ratio, confidence: year > 10 ? 'low' : lower.confidence };
}
