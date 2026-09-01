import { useMemo, useRef } from 'react';
import type { FormEvent } from 'react';
import type { GrowthEnvelopeForecast } from '@green/api-client';
import { forecastAt, GROWTH_HORIZON_MAX, GROWTH_HORIZON_MIN, GROWTH_HORIZON_STEP, normalizeGrowthHorizon } from './growthForecast';

export type GrowthHorizon = number | undefined;

export type ForecastSource = { canopy_forecast?: GrowthEnvelopeForecast[] | null; root_forecast?: GrowthEnvelopeForecast[] | null };

export function GrowthHorizonSlider({ value, onChange, ariaLabel = 'Горизонт прогноза' }: { value: GrowthHorizon; onChange: (value: GrowthHorizon) => void; ariaLabel?: string }) {
  const year = normalizeGrowthHorizon(value) ?? GROWTH_HORIZON_MIN;
  const lastEmittedValue = useRef<number | undefined>(undefined);
  const handleHorizonChange = (event: FormEvent<HTMLInputElement>) => {
    const raw = event.currentTarget.valueAsNumber;
    const next = normalizeGrowthHorizon(Number.isFinite(raw) ? raw : Number(event.currentTarget.value));
    if (next === undefined || next === lastEmittedValue.current) return;
    lastEmittedValue.current = next;
    onChange(next);
  };
  return <input aria-label={ariaLabel} aria-valuetext={year === 0 ? 'Сейчас' : `${year} лет`} type="range" min={GROWTH_HORIZON_MIN} max={GROWTH_HORIZON_MAX} step={GROWTH_HORIZON_STEP} value={year} onInput={handleHorizonChange} onChange={handleHorizonChange} />;
}

export function GrowthHorizonControl({ value, forecasts = [], onChange }: { value: GrowthHorizon; forecasts?: ForecastSource[]; onChange: (value: GrowthHorizon) => void }) {
  const year = normalizeGrowthHorizon(value) ?? GROWTH_HORIZON_MIN;
  const canopy = useMemo(() => forecasts.flatMap((source) => {
    const forecast = forecastAt(source.canopy_forecast ?? undefined, year);
    return forecast ? [forecast] : [];
  }), [forecasts, year]);
  const roots = useMemo(() => forecasts.flatMap((source) => {
    const forecast = forecastAt(source.root_forecast ?? undefined, year);
    return forecast ? [forecast] : [];
  }), [forecasts, year]);
  const range = (items: GrowthEnvelopeForecast[]) => items.length ? `${(Math.min(...items.map((item) => item.radius_min_m)) * 2).toFixed(1)}–${(Math.max(...items.map((item) => item.radius_max_m)) * 2).toFixed(1)} м` : undefined;
  const hasForecast = canopy.length > 0 || roots.length > 0;
  return <section className="growth-horizon-control" aria-label="Прогноз роста"><div className="growth-horizon-control__heading"><h3>Горизонт</h3><output aria-live="polite">{year === 0 ? 'Сейчас' : `${year} лет`}</output></div><GrowthHorizonSlider value={year} onChange={onChange} /><div className="growth-horizon-control__ticks" aria-hidden="true"><span>0</span><span>10</span><span>20</span><span>30</span><span>40</span></div>{hasForecast ? <dl className="growth-horizon-control__values">{canopy.length ? <><dt>Диаметр кроны</dt><dd>{range(canopy)}</dd></> : null}{roots.length ? <><dt>Корневая зона</dt><dd>{range(roots)}</dd></> : null}</dl> : <p role="status">Нет данных для этого горизонта.</p>}<p>Прогнозный диапазон между опорными точками, не нормативный отступ</p></section>;
}
