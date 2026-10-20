import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecommendationPanel } from './RecommendationPanel';
import { useRecommendationForm } from '../model/useRecommendationForm';

afterEach(cleanup);

describe('RecommendationPanel', () => {
  const zones = [
    {
      id: 'west',
      label: 'Запад',
      geometry: { type: 'Polygon' as const, coordinates: [] },
    },
  ];
  it('keeps the external form draft when the workspace temporarily unmounts its surface', () => {
    const onPreview = vi.fn();
    function Host({ visible }: { visible: boolean }) {
      const form = useRecommendationForm();
      return visible ? (
        <RecommendationPanel
          form={form}
          zones={zones}
          onPreview={onPreview}
          onCancel={vi.fn()}
        />
      ) : null;
    }
    const view = render(<Host visible />);
    fireEvent.change(screen.getByLabelText('Приоритет'), {
      target: { value: 'continuity' },
    });
    fireEvent.change(screen.getByLabelText('Максимум посадок'), {
      target: { value: '65' },
    });
    view.rerender(<Host visible={false} />);
    view.rerender(<Host visible />);
    expect(screen.getByLabelText('Приоритет')).toHaveValue('continuity');
    expect(screen.getByLabelText('Максимум посадок')).toHaveValue(65);
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));
    expect(onPreview).toHaveBeenCalledWith({
      profile: 'continuity',
      max_sites: 65,
      zone_ids: ['west'],
    });
  });
  it('builds one proposal for several selected zones', () => {
    const onPreview = vi.fn();
    render(
      <RecommendationPanel
        zones={[
          {
            id: 'west',
            label: 'Запад',
            geometry: { type: 'Polygon', coordinates: [] },
          },
          {
            id: 'east',
            label: 'Восток',
            geometry: { type: 'Polygon', coordinates: [] },
          },
        ]}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Приоритет'), {
      target: { value: 'continuity' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));
    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({
        zone_ids: ['west', 'east'],
        profile: 'continuity',
        max_sites: 40,
      }),
    );
  });

  it('retains manual parameters when going back to the selected zones, then previews exactly once', () => {
    const onPreview = vi.fn();
    const onCancel = vi.fn();
    render(
      <RecommendationPanel
        guided
        zones={zones}
        onPreview={onPreview}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать задачу' }));
    fireEvent.change(screen.getByLabelText('Приоритет'), {
      target: { value: 'shade' },
    });
    fireEvent.change(screen.getByLabelText('Максимум посадок'), {
      target: { value: '25' },
    });
    expect(onPreview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
    expect(screen.getByRole('checkbox', { name: 'Запад' })).toBeChecked();
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать задачу' }));
    expect(screen.getByLabelText('Приоритет')).toHaveValue('shade');
    expect(screen.getByLabelText('Максимум посадок')).toHaveValue(25);
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledExactlyOnceWith({
      profile: 'shade',
      max_sites: 25,
      zone_ids: ['west'],
    });
  });

  it('uses the controlled zone selection and waits for a selected zone before advancing', () => {
    const onZonesChange = vi.fn();
    const props = {
      guided: true,
      zones,
      onPreview: vi.fn(),
      onCancel: vi.fn(),
      onSelectedZoneIdsChange: onZonesChange,
    };
    const view = render(
      <RecommendationPanel {...props} selectedZoneIds={[]} />,
    );
    expect(
      screen.getByRole('button', { name: 'Выбрать задачу' }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Запад' }));
    expect(onZonesChange).toHaveBeenCalledExactlyOnceWith(['west']);
    expect(
      screen.getByRole('button', { name: 'Выбрать задачу' }),
    ).toBeDisabled();
    view.rerender(
      <RecommendationPanel {...props} selectedZoneIds={['west']} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать задачу' }));
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(props.onPreview).toHaveBeenCalledExactlyOnceWith({
      profile: 'balanced',
      max_sites: 40,
      zone_ids: ['west'],
    });
  });

  it('cancels the spatial calculation without leaving the tool or starting another preview', () => {
    const onPreview = vi.fn();
    const onCancel = vi.fn();
    const onCancelCalculation = vi.fn();
    render(
      <RecommendationPanel
        zones={zones}
        loading
        calculating
        onPreview={onPreview}
        onCancel={onCancel}
        onCancelCalculation={onCancelCalculation}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Отменить расчёт' }));
    expect(onCancelCalculation).toHaveBeenCalledOnce();
    expect(onPreview).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Проверяем' })).toBeDisabled();
  });
});
