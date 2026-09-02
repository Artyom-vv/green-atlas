import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PlantingZonePicker } from './PlantingZonePicker';

const zones = [
  { id: 'a', label: 'Ручной участок', geometry: { type: 'Polygon', coordinates: [] } },
  { id: 'b', label: 'Ручной участок', geometry: { type: 'Polygon', coordinates: [] } },
];

describe('PlantingZonePicker', () => {
  it('keeps selection and creation in one accessible component', () => {
    const onChange = vi.fn();
    const onCreate = vi.fn();
    render(<PlantingZonePicker zones={zones} selectedIds={['a']} onChange={onChange} onCreate={onCreate} />);
    expect(screen.getByText('1 / 2')).toBeVisible();
    expect(screen.getByRole('checkbox', { name: 'Ручной участок 1' })).toBeChecked();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Ручной участок 2' }));
    expect(onChange).toHaveBeenCalledWith(['a', 'b']);
    fireEvent.click(screen.getByRole('button', { name: 'Новый участок' }));
    expect(onCreate).toHaveBeenCalledOnce();
  });
});
