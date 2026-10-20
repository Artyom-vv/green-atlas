/** Прогноз посадок: шкала года, диапазон кроны и диаметр рабочей корневой зоны. */
import type { FC } from 'react';
import type { GrowthEnvelopeForecast } from '@green/api-client';
import { Slider, tv } from '@green/ui';
import {
  forecastAt,
  growthHorizonLabel,
  GROWTH_HORIZON_MAX,
  GROWTH_HORIZON_MIN,
  GROWTH_HORIZON_STEP,
  normalizeGrowthHorizon,
  workingEnvelopeRadius,
} from '../model/growthForecast';

export type GrowthHorizon = number | undefined;
export interface ForecastSource {
  canopy_forecast?: GrowthEnvelopeForecast[] | null;
  root_forecast?: GrowthEnvelopeForecast[] | null;
}

export interface GrowthHorizonSliderProps {
  value: GrowthHorizon;
  onChange: (value: GrowthHorizon) => void;
  ariaLabel?: string;
  disabled?: boolean;
  onCommit?: (year: number) => void;
  scale?: 'milestones' | 'inline';
}

const horizonAppearance = tv({
  slots: {
    root: 'grid min-w-0 gap-2 select-none',
    heading: 'flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1',
    label: 'm-0 text-xs font-medium text-neutral-600',
    value:
      'text-sm font-medium whitespace-nowrap text-neutral-800 tabular-nums',
    slider:
      'group @container/horizon min-w-0 select-none data-disabled:opacity-50',
    control: 'relative mx-2 flex h-8 touch-none items-center',
    track: 'relative h-1 w-full overflow-hidden rounded-full bg-neutral-200',
    indicator: 'h-full rounded-full bg-blue-600',
    thumb:
      'relative size-4 cursor-ew-resize rounded-full border-2 border-solid border-white bg-blue-600 outline-none before:absolute before:-inset-2 before:rounded-full focus-within:ring-2 focus-within:ring-blue-600 focus-within:ring-offset-1 hover:bg-blue-700 data-disabled:cursor-default data-dragging:bg-blue-700',
    milestones: 'relative mx-2 h-4 text-xs text-neutral-500 tabular-nums',
    milestone:
      'absolute top-0 -translate-x-1/2 whitespace-nowrap first:translate-x-0 last:-translate-x-full',
    inlineBoundary:
      'hidden shrink-0 text-xs whitespace-nowrap text-neutral-500 tabular-nums @min-[240px]/forecast:block',
    metrics:
      'm-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-xs leading-4 text-neutral-600 [&_dd]:m-0 [&_dd]:font-mono [&_dd]:text-neutral-800',
    note: 'm-0 text-xs leading-4 text-neutral-500',
  },
  variants: {
    appearance: {
      embedded: {},
      viewport: {
        root: 'rounded-control @container/forecast h-[42px] grid-cols-[auto_minmax(0,1fr)_4rem] items-center gap-2 border border-solid border-neutral-300 bg-white p-1',
        heading: 'contents',
        label: 'pl-2',
        value: 'col-start-3 row-start-1 text-center',
      },
    },
    scale: {
      milestones: {},
      inline: {
        slider: 'col-start-2 row-start-1 flex items-center gap-1',
        control: 'flex-1',
      },
    },
  },
  defaultVariants: { appearance: 'embedded', scale: 'milestones' },
});

const HORIZON_MILESTONES = [GROWTH_HORIZON_MIN, 10, 20, 30, GROWTH_HORIZON_MAX];

export const GrowthHorizonSlider: FC<GrowthHorizonSliderProps> = ({
  value,
  onChange,
  ariaLabel = 'Горизонт прогноза',
  disabled = false,
  onCommit,
  scale = 'milestones',
}) => {
  const year = normalizeGrowthHorizon(value) ?? GROWTH_HORIZON_MIN;
  const appearance = horizonAppearance({ scale });
  return (
    <Slider.Root
      className={appearance.slider()}
      data-growth-horizon-slider=""
      min={GROWTH_HORIZON_MIN}
      max={GROWTH_HORIZON_MAX}
      step={GROWTH_HORIZON_STEP}
      value={year}
      disabled={disabled}
      onValueChange={(next) => onChange(next)}
      onValueCommitted={onCommit ? (next) => onCommit(next) : undefined}
    >
      {scale === 'inline' && (
        <span className={appearance.inlineBoundary()} aria-hidden="true">
          {GROWTH_HORIZON_MIN}
        </span>
      )}
      <Slider.Control className={appearance.control()}>
        <Slider.Track className={appearance.track()}>
          <Slider.Indicator className={appearance.indicator()} />
        </Slider.Track>
        <Slider.Thumb
          className={appearance.thumb()}
          aria-label={ariaLabel}
          aria-valuetext={growthHorizonLabel(year)}
        />
      </Slider.Control>
      {scale === 'inline' ? (
        <span className={appearance.inlineBoundary()} aria-hidden="true">
          {GROWTH_HORIZON_MAX} лет
        </span>
      ) : (
        <div className={appearance.milestones()} aria-hidden="true">
          {HORIZON_MILESTONES.map((milestone) => (
            <span
              key={milestone}
              className={appearance.milestone({
                className:
                  milestone === 10 || milestone === 30
                    ? 'hidden @min-[240px]/horizon:block'
                    : undefined,
              })}
              style={{
                left: `${((milestone - GROWTH_HORIZON_MIN) / (GROWTH_HORIZON_MAX - GROWTH_HORIZON_MIN)) * 100}%`,
              }}
            >
              {milestone === GROWTH_HORIZON_MAX
                ? `${milestone} лет`
                : milestone}
            </span>
          ))}
        </div>
      )}
    </Slider.Root>
  );
};

export interface GrowthHorizonControlProps {
  className?: string;
  appearance?: 'embedded' | 'viewport';
  value: GrowthHorizon;
  forecasts?: ForecastSource[];
  onChange: (value: GrowthHorizon) => void;
  missingReason?: string;
  showMetrics?: boolean;
  showSlider?: boolean;
  label?: string;
  disabled?: boolean;
  onCommit?: (year: number) => void;
}

function forecastRange(items: GrowthEnvelopeForecast[]) {
  return items.length
    ? `${(Math.min(...items.map((item) => item.radius_min_m)) * 2).toFixed(1)}–${(Math.max(...items.map((item) => item.radius_max_m)) * 2).toFixed(1)} м`
    : undefined;
}

export const GrowthHorizonControl: FC<GrowthHorizonControlProps> = ({
  className,
  appearance: surface = 'embedded',
  value,
  forecasts = [],
  onChange,
  missingReason,
  showMetrics = true,
  showSlider = true,
  label = 'Прогноз роста',
  disabled = false,
  onCommit,
}) => {
  const year = normalizeGrowthHorizon(value) ?? GROWTH_HORIZON_MIN;
  const appearance = horizonAppearance({ appearance: surface });
  const canopy = forecasts.flatMap((source) => {
    const result = forecastAt(source.canopy_forecast ?? undefined, year);
    return result ? [result] : [];
  });
  const roots = forecasts.flatMap((source) => {
    const radius = workingEnvelopeRadius(
      'roots',
      forecastAt(source.root_forecast ?? undefined, year),
    );
    return radius === undefined ? [] : [radius];
  });
  const hasForecast = canopy.length > 0 || roots.length > 0;
  return (
    <section className={appearance.root({ className })} aria-label={label}>
      <div className={appearance.heading()}>
        <h3 className={appearance.label()}>
          {surface === 'viewport' && label === 'Прогноз роста' ? 'Рост' : label}
        </h3>
        <output className={appearance.value()} aria-live="polite">
          {growthHorizonLabel(year)}
        </output>
      </div>
      {showSlider ? (
        <GrowthHorizonSlider
          value={year}
          onChange={onChange}
          onCommit={onCommit}
          disabled={disabled}
          scale={surface === 'viewport' ? 'inline' : 'milestones'}
          ariaLabel={
            surface === 'viewport'
              ? label
              : label === 'Прогноз роста'
                ? undefined
                : label
          }
        />
      ) : null}
      {showMetrics ? (
        hasForecast ? (
          <dl className={appearance.metrics()}>
            {canopy.length ? (
              <>
                <dt>Диаметр кроны</dt>
                <dd>{forecastRange(canopy)}</dd>
              </>
            ) : null}
            {roots.length ? (
              <>
                <dt>Диаметр корневой зоны</dt>
                <dd>{(Math.max(...roots) * 2).toFixed(1)} м</dd>
              </>
            ) : null}
          </dl>
        ) : (
          <p role="status" className={appearance.note()}>
            {missingReason ?? 'Для выбранных посадок нет данных на этот год'}
          </p>
        )
      ) : null}
      {showMetrics && hasForecast && canopy.length < forecasts.length ? (
        <p role="status" className={appearance.note()}>
          Прогноз для {canopy.length} из {forecasts.length}. {missingReason}
        </p>
      ) : null}
    </section>
  );
};
