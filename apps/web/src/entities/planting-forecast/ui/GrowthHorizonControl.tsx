import { useEffect, useRef, type FC, type FormEvent } from 'react';
import type { GrowthEnvelopeForecast } from '@green/api-client';
import { cx } from '@green/ui';
import {
  forecastAt,
  GROWTH_HORIZON_MAX,
  GROWTH_HORIZON_MIN,
  GROWTH_HORIZON_STEP,
  normalizeGrowthHorizon,
} from '../model/growthForecast';

export type GrowthHorizon = number | undefined;
export interface ForecastSource {
  canopy_forecast?: GrowthEnvelopeForecast[] | null;
  root_forecast?: GrowthEnvelopeForecast[] | null;
}

function horizonLabel(year: number) {
  if (year === 0) return 'Сейчас';
  const lastTwo = year % 100;
  const last = year % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return `${year} лет`;
  if (last === 1) return `${year} год`;
  if (last >= 2 && last <= 4) return `${year} года`;
  return `${year} лет`;
}

export interface GrowthHorizonSliderProps {
  value: GrowthHorizon;
  onChange: (value: GrowthHorizon) => void;
  ariaLabel?: string;
}

export const GrowthHorizonSlider: FC<GrowthHorizonSliderProps> = ({
  value,
  onChange,
  ariaLabel = 'Горизонт прогноза',
}) => {
  const year = normalizeGrowthHorizon(value) ?? GROWTH_HORIZON_MIN;
  const lastEmittedValue = useRef<number | undefined>(undefined);
  useEffect(() => {
    lastEmittedValue.current = year;
  }, [year]);
  const handleHorizonChange = (event: FormEvent<HTMLInputElement>) => {
    const raw = event.currentTarget.valueAsNumber;
    const next = normalizeGrowthHorizon(
      Number.isFinite(raw) ? raw : Number(event.currentTarget.value),
    );
    if (next === undefined || next === lastEmittedValue.current) return;
    lastEmittedValue.current = next;
    onChange(next);
  };
  return (
    <input
      className="m-0 h-5 w-full cursor-ew-resize accent-blue-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      aria-label={ariaLabel}
      aria-valuetext={horizonLabel(year)}
      type="range"
      min={GROWTH_HORIZON_MIN}
      max={GROWTH_HORIZON_MAX}
      step={GROWTH_HORIZON_STEP}
      value={year}
      onInput={handleHorizonChange}
      onChange={handleHorizonChange}
    />
  );
};

export interface GrowthHorizonControlProps {
  className?: string;
  value: GrowthHorizon;
  forecasts?: ForecastSource[];
  onChange: (value: GrowthHorizon) => void;
  missingReason?: string;
  showMetrics?: boolean;
  showSlider?: boolean;
}

function forecastRange(items: GrowthEnvelopeForecast[]) {
  return items.length
    ? `${(Math.min(...items.map((item) => item.radius_min_m)) * 2).toFixed(1)}–${(Math.max(...items.map((item) => item.radius_max_m)) * 2).toFixed(1)} м`
    : undefined;
}

export const GrowthHorizonControl: FC<GrowthHorizonControlProps> = ({
  className,
  value,
  forecasts = [],
  onChange,
  missingReason,
  showMetrics = true,
  showSlider = true,
}) => {
  const year = normalizeGrowthHorizon(value) ?? GROWTH_HORIZON_MIN;
  const canopy = forecasts.flatMap((source) => {
    const result = forecastAt(source.canopy_forecast ?? undefined, year);
    return result ? [result] : [];
  });
  const roots = forecasts.flatMap((source) => {
    const result = forecastAt(source.root_forecast ?? undefined, year);
    return result ? [result] : [];
  });
  const hasForecast = canopy.length > 0 || roots.length > 0;
  return (
    <section
      className={cx('grid min-w-0 gap-2 py-3', className)}
      aria-label="Прогноз роста"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="m-0 text-sm font-semibold">Прогноз роста</h3>
        <output className="font-mono text-sm text-blue-600" aria-live="polite">
          {horizonLabel(year)}
        </output>
      </div>
      {showSlider ? (
        <>
          <GrowthHorizonSlider value={year} onChange={onChange} />
          <div
            className="flex justify-between font-mono text-[11px] text-neutral-500"
            aria-hidden="true"
          >
            <span>0</span>
            <span>10</span>
            <span>20</span>
            <span>30</span>
            <span>40</span>
          </div>
        </>
      ) : null}
      {showMetrics ? (
        hasForecast ? (
          <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-xs leading-4 text-neutral-600 [&_dd]:m-0 [&_dd]:font-mono [&_dd]:text-neutral-800">
            {canopy.length ? (
              <>
                <dt>Диаметр кроны</dt>
                <dd>{forecastRange(canopy)}</dd>
              </>
            ) : null}
            {roots.length ? (
              <>
                <dt>Корневая зона</dt>
                <dd>{forecastRange(roots)}</dd>
              </>
            ) : null}
          </dl>
        ) : (
          <p role="status" className="m-0 text-xs leading-4 text-neutral-500">
            {missingReason ?? 'Для выбранных посадок нет данных на этот год.'}
          </p>
        )
      ) : null}
      {showMetrics && hasForecast && canopy.length < forecasts.length ? (
        <p role="status" className="m-0 text-xs leading-4 text-neutral-500">
          Прогноз для {canopy.length} из {forecasts.length}. {missingReason}
        </p>
      ) : null}
    </section>
  );
};
