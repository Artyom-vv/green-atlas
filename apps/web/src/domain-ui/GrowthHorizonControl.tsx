import type { GrowthEnvelopeForecast } from '@green/api-client';
import { forecastAt } from './growthForecast';

export type GrowthHorizon = number | undefined;

type ForecastSource = { canopy_forecast?: GrowthEnvelopeForecast[] | null; root_forecast?: GrowthEnvelopeForecast[] | null };

export function GrowthHorizonControl({ value, forecasts = [], onChange }: { value: GrowthHorizon; forecasts?: ForecastSource[]; onChange: (value: GrowthHorizon) => void }) {
  const year = value ?? 0;
  const canopy = forecasts.flatMap((source) => { const value = forecastAt(source.canopy_forecast ?? undefined, year); return value ? [value] : []; });
  const roots = forecasts.flatMap((source) => { const value = forecastAt(source.root_forecast ?? undefined, year); return value ? [value] : []; });
  const range = (items: GrowthEnvelopeForecast[]) => items.length ? `${(Math.min(...items.map((item) => item.radius_min_m)) * 2).toFixed(1)}–${(Math.max(...items.map((item) => item.radius_max_m)) * 2).toFixed(1)} м` : undefined;
  return <section className="growth-horizon-control" aria-label="Прогноз роста"><div className="growth-horizon-control__heading"><h3>Горизонт</h3><output>{year === 0 ? 'Сейчас' : `${year} лет`}</output></div><input aria-label="Горизонт прогноза" type="range" min="0" max="40" step="1" value={year} onChange={(event) => onChange(Number(event.target.value))} /><div className="growth-horizon-control__ticks" aria-hidden="true"><span>0</span><span>10</span><span>20</span><span>30</span><span>40</span></div>{canopy.length || roots.length ? <dl className="growth-horizon-control__values">{canopy.length ? <><dt>Диаметр кроны</dt><dd>{range(canopy)}</dd></> : null}{roots.length ? <><dt>Корневая зона</dt><dd>{range(roots)}</dd></> : null}</dl> : null}<p>Прогнозный диапазон, не нормативный отступ</p></section>;
}
