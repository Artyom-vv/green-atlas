import type { GrowthEnvelopeForecast } from '@green/api-client';

export const GROWTH_HORIZON_MIN = 0;
export const GROWTH_HORIZON_MAX = 40;
export const GROWTH_HORIZON_STEP = 1;

export function normalizeGrowthHorizon(
  value: number | undefined,
): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  return Math.min(
    GROWTH_HORIZON_MAX,
    Math.max(GROWTH_HORIZON_MIN, Math.round(value)),
  );
}

export function forecastAt(
  items: GrowthEnvelopeForecast[] | undefined,
  year: number,
): GrowthEnvelopeForecast | undefined {
  if (!items?.length || !Number.isFinite(year)) return undefined;
  const sorted = [...items].sort((a, b) => a.horizon_year - b.horizon_year);
  // A nearest-anchor fallback would make the UI look precise when the
  // catalogue has no evidence for that horizon. Keep the gap explicit.
  if (
    year < sorted[0].horizon_year ||
    year > sorted[sorted.length - 1].horizon_year
  )
    return undefined;
  const lower =
    [...sorted].reverse().find((item) => item.horizon_year <= year) ??
    sorted[0];
  const upper =
    sorted.find((item) => item.horizon_year >= year) ??
    sorted[sorted.length - 1];
  if (lower.horizon_year === upper.horizon_year) return lower;
  const ratio =
    (year - lower.horizon_year) / (upper.horizon_year - lower.horizon_year);
  return {
    ...lower,
    horizon_year: year,
    radius_min_m:
      lower.radius_min_m + (upper.radius_min_m - lower.radius_min_m) * ratio,
    radius_max_m:
      lower.radius_max_m + (upper.radius_max_m - lower.radius_max_m) * ratio,
    confidence: year > 10 ? 'low' : lower.confidence,
  };
}
