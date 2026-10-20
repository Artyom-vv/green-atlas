/** Прогноз посадок: общий диапазон лет и интерполяция данных каталога для просмотра. */
import type { GrowthEnvelopeForecast } from '@green/api-client';

export const GROWTH_HORIZON_MIN = 0;
export const GROWTH_HORIZON_MAX = 40;
export const GROWTH_HORIZON_STEP = 1;

const yearFormat = new Intl.NumberFormat('ru-RU', {
  style: 'unit',
  unit: 'year',
  unitDisplay: 'long',
});
export const growthHorizonLabel = (year: number) =>
  year === 0 ? 'Сейчас' : yearFormat.format(year);

export function normalizeGrowthHorizon(
  value: number | undefined,
): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  return Math.min(
    GROWTH_HORIZON_MAX,
    Math.max(GROWTH_HORIZON_MIN, Math.round(value)),
  );
}

/** Рабочий охват использует верхнюю границу кроны и нижнюю границу корней. */
export function workingEnvelopeRadius(
  kind: 'canopy' | 'roots',
  forecast?: Partial<
    Pick<GrowthEnvelopeForecast, 'radius_min_m' | 'radius_max_m'>
  >,
): number | undefined {
  const radius =
    kind === 'roots' ? forecast?.radius_min_m : forecast?.radius_max_m;
  return radius !== undefined && Number.isFinite(radius) && radius >= 0
    ? radius
    : undefined;
}

export function forecastAt(
  items: GrowthEnvelopeForecast[] | undefined,
  year: number,
): GrowthEnvelopeForecast | undefined {
  if (!items?.length || !Number.isFinite(year)) return undefined;
  const sorted = [...items].sort((a, b) => a.horizon_year - b.horizon_year);
  // За пределами известных лет каталог не даёт основания для интерполяции.
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
