import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlantingZonePicker } from '@/entities/planting-zone/ui/PlantingZonePicker';

const zones = [
  {
    id: 'a',
    label: 'Ручной участок',
    geometry: { type: 'Polygon', coordinates: [] },
  },
  {
    id: 'b',
    label: 'Ручной участок',
    geometry: { type: 'Polygon', coordinates: [] },
  },
];

afterEach(cleanup);

describe('PlantingZonePicker', () => {
  it('does not nest a collapsible section inside a dedicated workflow step', () => {
    const { container } = render(
      <PlantingZonePicker
        expanded
        zones={zones}
        selectedIds={['a']}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('heading', { name: 'Где разместить посадки?' }),
    ).toBeInTheDocument();
    expect(container.querySelector('details')).toBeNull();
    expect(
      screen.getByRole('checkbox', { name: 'Ручной участок 2' }),
    ).toBeVisible();
  });
  it('does not close the checklist after the first selection', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <PlantingZonePicker zones={zones} selectedIds={[]} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Ручной участок 1' }));
    rerender(
      <PlantingZonePicker
        zones={zones}
        selectedIds={['a']}
        onChange={onChange}
      />,
    );
    expect(
      screen.getByRole('checkbox', { name: 'Ручной участок 2' }),
    ).toBeVisible();
  });
  it('keeps selection and creation in one accessible component', () => {
    const onChange = vi.fn();
    const onCreate = vi.fn();
    render(
      <PlantingZonePicker
        zones={zones}
        selectedIds={['a']}
        onChange={onChange}
        onCreate={onCreate}
      />,
    );
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ручной участок 1' }));
    expect(
      screen.getByRole('checkbox', { name: 'Ручной участок 1' }),
    ).toBeChecked();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Ручной участок 2' }));
    expect(onChange).toHaveBeenCalledWith(['a', 'b']);
    fireEvent.click(screen.getByRole('button', { name: 'Новый участок' }));
    expect(onCreate).toHaveBeenCalledOnce();
  });
});
