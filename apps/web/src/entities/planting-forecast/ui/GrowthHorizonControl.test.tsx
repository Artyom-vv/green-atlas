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

function renderControlled(initial = 0, onCommit?: (year: number) => void) {
  const onChange = vi.fn();
  function Harness() {
    const [value, setValue] = useState(initial);
    return (
      <GrowthHorizonControl
        value={value}
        forecasts={forecasts}
        onCommit={onCommit}
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
  it('does not cancel the native range mouse gesture', () => {
    renderControlled();
    const slider = screen.getByRole('slider', { name: 'Горизонт прогноза' });
    const mouse = new MouseEvent('mousedown', {
      bubbles: true,
      cancelable: true,
      button: 0,
    });
    slider.dispatchEvent(mouse);
    expect(mouse.defaultPrevented).toBe(false);
    fireEvent.input(slider, { target: { value: '23' } });
    expect(slider).toHaveValue('23');
    expect(screen.getByText('23 года')).toBeVisible();
  });
  it('keeps the full accessible forecast name and keyboard commits in the compact viewport control', () => {
    const onChange = vi.fn();
    const onCommit = vi.fn();
    const props = {
      appearance: 'viewport' as const,
      showMetrics: false,
      onChange,
      onCommit,
    };
    const view = render(<GrowthHorizonControl {...props} value={0} />);

    expect(screen.getByRole('heading', { name: 'Рост' })).toBeVisible();
    expect(screen.getByRole('region', { name: 'Прогноз роста' })).toBeVisible();
    const slider = screen.getByRole('slider', { name: 'Прогноз роста' });
    expect(slider).toHaveAttribute('aria-valuetext', 'Сейчас');
    fireEvent.keyDown(slider, { key: 'End' });
    expect(onChange).toHaveBeenCalledExactlyOnceWith(40);
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(40);

    view.rerender(<GrowthHorizonControl {...props} value={40} />);
    expect(slider).toHaveValue('40');
    expect(slider).toHaveAttribute('aria-valuetext', '40 лет');
    expect(screen.getByRole('status')).toHaveTextContent('40 лет');
  });

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
      screen.getByText('Диаметр корневой зоны').nextElementSibling,
    ).toHaveTextContent('5.1 м');
  });

  it('shows the largest working root diameter for a mixed selection', () => {
    render(
      <GrowthHorizonControl
        value={20}
        forecasts={[
          forecasts[0],
          {
            ...forecasts[0],
            root_forecast: [
              {
                ...forecasts[0].root_forecast[1],
                radius_min_m: 3,
                radius_max_m: 20,
              },
            ],
          },
        ]}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByText('Диаметр корневой зоны').nextElementSibling,
    ).toHaveTextContent('6.0 м');
    expect(forecasts[0].root_forecast[1].radius_max_m).toBe(8.4);
  });

  it('uses real keyboard steps and does not double-apply the matching input event', () => {
    const { onChange } = renderControlled(22);
    const slider = screen.getByRole('slider', { name: 'Горизонт прогноза' });

    slider.focus();
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    fireEvent.change(slider, { target: { value: '23' } });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(23);
    expect(screen.getByText('23 года')).toBeVisible();
  });

  it('previews a drag immediately and commits only its final year', () => {
    const onCommit = vi.fn();
    const { onChange } = renderControlled(0, onCommit);
    const slider = screen.getByRole('slider', { name: 'Горизонт прогноза' });
    const control = slider.parentElement?.parentElement;
    if (!control) throw new Error('Нет поверхности шкалы');
    vi.spyOn(control, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      right: 400,
      top: 0,
      bottom: 40,
      width: 400,
      height: 40,
      toJSON: () => ({}),
    });

    fireEvent.touchStart(control, {
      changedTouches: [{ identifier: 1, clientX: 200, clientY: 20 }],
    });
    fireEvent.touchMove(document, {
      changedTouches: [{ identifier: 1, clientX: 230, clientY: 20 }],
    });
    expect(onChange).toHaveBeenLastCalledWith(23);
    expect(screen.getByText('23 года')).toBeVisible();
    expect(onCommit).not.toHaveBeenCalled();

    fireEvent.touchEnd(document, {
      changedTouches: [{ identifier: 1, clientX: 230, clientY: 20 }],
    });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(23);
  });

  it('supports keyboard bounds, the accessible year and one commit per step', () => {
    const onCommit = vi.fn();
    renderControlled(20, onCommit);
    const slider = screen.getByRole('slider', { name: 'Горизонт прогноза' });
    fireEvent.keyDown(slider, { key: 'End' });
    expect(slider).toHaveValue('40');
    expect(slider).toHaveAttribute('aria-valuetext', '40 лет');
    expect(onCommit).toHaveBeenLastCalledWith(40);
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(onCommit).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(slider, { key: 'Home' });
    expect(slider).toHaveValue('0');
    expect(slider).toHaveAttribute('aria-valuetext', 'Сейчас');
    expect(onCommit).toHaveBeenLastCalledWith(0);
  });

  it('lets the caller name and disable the calculation horizon without a second control', () => {
    render(
      <GrowthHorizonControl
        value={10}
        onChange={vi.fn()}
        label="Горизонт расчёта"
        disabled
        showMetrics={false}
      />,
    );
    expect(
      screen.getByRole('region', { name: 'Горизонт расчёта' }),
    ).toBeVisible();
    expect(
      screen.getByRole('slider', { name: 'Горизонт расчёта' }),
    ).toBeDisabled();
    expect(screen.getAllByRole('slider')).toHaveLength(1);
    expect(screen.getByText('10 лет')).toBeVisible();
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
      screen.getByText('Для выбранных посадок нет данных на этот год'),
    ).toBeVisible();
    expect(screen.queryByText('Диаметр кроны')).not.toBeInTheDocument();
  });
});
