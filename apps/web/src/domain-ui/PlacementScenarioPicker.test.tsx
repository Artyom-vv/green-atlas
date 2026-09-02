import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlacementScenarioPicker } from './PlacementScenarioPicker';

afterEach(cleanup);

describe('PlacementScenarioPicker', () => {
  it('offers four spatial intents as an accessible single-choice control', () => {
    const onChange = vi.fn();
    render(<PlacementScenarioPicker value="natural" onChange={onChange} />);

    expect(screen.getAllByRole('radio')).toHaveLength(4);
    expect(screen.getByRole('radio', { name: 'Свободно' })).toBeChecked();
    expect(screen.getByText('Сценарий только предлагает места. Отступы и ограничения проверяются для каждой позиции.')).toBeVisible();

    fireEvent.click(screen.getByRole('radio', { name: 'Куртины' }));
    expect(onChange).toHaveBeenCalledWith('cluster_groves');
  });

  it('uses catalog metadata and prevents choosing an unavailable scenario', () => {
    const onChange = vi.fn();
    render(<PlacementScenarioPicker value="natural" presets={[
      { id: 'road_edges', title: 'Аллеи вдоль проездов', description: 'Вдоль всех распознанных дорог', available: false, unavailable_reason: 'В DXF нет распознанных дорог' },
      { id: 'regular_grid', title: 'Сетка по осям', description: 'Рабочее описание из API', available: true },
      { id: 'cluster_groves', title: 'Куртины', description: 'Компактные группы', available: true },
    ]} onChange={onChange} />);

    const roads = screen.getByRole('radio', { name: 'Аллеи вдоль проездов' });
    expect(roads).toBeDisabled();
    expect(screen.getByText('В DXF нет распознанных дорог')).toBeVisible();
    fireEvent.click(roads);
    expect(onChange).not.toHaveBeenCalled();

    expect(screen.getByText('Рабочее описание из API')).toBeVisible();
  });

  it('keeps separate picker instances in separate browser radio groups', () => {
    render(<><PlacementScenarioPicker value="natural" onChange={vi.fn()} /><PlacementScenarioPicker value="natural" onChange={vi.fn()} /></>);
    const natural = screen.getAllByRole('radio', { name: 'Свободно' });
    const clusters = screen.getAllByRole('radio', { name: 'Куртины' });

    expect(natural).toHaveLength(2);
    expect(natural[0]).toBeChecked();
    expect(natural[1]).toBeChecked();
    fireEvent.click(clusters[0]);
    expect(natural[1]).toBeChecked();
  });
});
