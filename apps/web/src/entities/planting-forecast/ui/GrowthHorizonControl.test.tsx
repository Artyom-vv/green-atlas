import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GrowthHorizonControl } from '@/entities/planting-forecast/ui/GrowthHorizonControl';

afterEach(cleanup);

const forecasts = [
  {
    canopy_forecast: [
      {
        horizon_year: 0,
        radius_min_m: 0.4,
        radius_max_m: 2.1,
        confidence: 'medium' as const,
        basis: 'test',
      },
      {
        horizon_year: 20,
        radius_min_m: 3.3,
        radius_max_m: 7,
        confidence: 'low' as const,
        basis: 'test',
      },
      {
        horizon_year: 30,
        radius_min_m: 3.6,
        radius_max_m: 7.2,
        confidence: 'low' as const,
        basis: 'test',
      },
      {
        horizon_year: 40,
        radius_min_m: 3.8,
        radius_max_m: 7.4,
        confidence: 'low' as const,
        basis: 'test',
      },
    ],
    root_forecast: [
      {
        horizon_year: 0,
        radius_min_m: 0.3,
        radius_max_m: 2.5,
        confidence: 'low' as const,
        basis: 'test',
      },
      {
        horizon_year: 20,
        radius_min_m: 2.5,
        radius_max_m: 8.4,
        confidence: 'low' as const,
        basis: 'test',
      },
      {
        horizon_year: 30,
        radius_min_m: 2.7,
        radius_max_m: 8.7,
        confidence: 'low' as const,
        basis: 'test',
      },
      {
        horizon_year: 40,
        radius_min_m: 2.8,
        radius_max_m: 8.9,
        confidence: 'low' as const,
        basis: 'test',
      },
    ],
  },
];

function renderControlled(initial = 0) {
  const onChange = vi.fn();
  function Harness() {
    const [value, setValue] = useState(initial);
    return (
      <GrowthHorizonControl
        value={value}
        forecasts={forecasts}
        onChange={(next) => {
          if (next === undefined) return;
          onChange(next);
          setValue(next);
        }}
      />
    );
  }
  return { onChange, ...render(<Harness />) };
}

describe('GrowthHorizonControl', () => {
  it('updates the controlled label and dimensions from a pointer/input event at year 23', () => {
    const { onChange } = renderControlled();
    const slider = screen.getByRole('slider', { name: 'Горизонт прогноза' });

    fireEvent.pointerDown(slider);
    fireEvent.input(slider, { target: { value: '23' } });
    fireEvent.pointerUp(slider);

    expect(onChange).toHaveBeenCalledWith(23);
    expect(slider).toHaveValue('23');
    expect(screen.getByText('23 года')).toBeVisible();
    expect(
      screen.getByText('Диаметр кроны').nextElementSibling,
    ).toHaveTextContent('6.8–14.1 м');
    expect(
      screen.getByText('Корневая зона').nextElementSibling,
    ).toHaveTextContent('5.1–17.0 м');
  });

  it('accepts keyboard-style input and does not double-apply a subsequent change event', () => {
    const { onChange } = renderControlled();
    const slider = screen.getByRole('slider', { name: 'Горизонт прогноза' });

    slider.focus();
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    fireEvent.input(slider, { target: { value: '23' } });
    fireEvent.change(slider, { target: { value: '23' } });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(23);
    expect(screen.getByText('23 года')).toBeVisible();
  });

  it('uses change as a fallback and leaves unsupported years without a forecast', () => {
    const onChange = vi.fn();
    function Harness() {
      const [value, setValue] = useState(0);
      return (
        <GrowthHorizonControl
          value={value}
          forecasts={[
            {
              canopy_forecast: [
                {
                  horizon_year: 10,
                  radius_min_m: 1,
                  radius_max_m: 2,
                  confidence: 'low',
                  basis: 'test',
                },
              ],
            },
          ]}
          onChange={(next) => {
            if (next === undefined) return;
            onChange(next);
            setValue(next);
          }}
        />
      );
    }
    render(<Harness />);
    const slider = screen.getByRole('slider', { name: 'Горизонт прогноза' });

    fireEvent.change(slider, { target: { value: '23' } });

    expect(onChange).toHaveBeenCalledWith(23);
    expect(
      screen.getByText('Для выбранных посадок нет данных на этот год.'),
    ).toBeVisible();
    expect(screen.queryByText('Диаметр кроны')).not.toBeInTheDocument();
  });
});
