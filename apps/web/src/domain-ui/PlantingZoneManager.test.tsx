import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlantingZoneManager } from './PlantingZoneManager';

afterEach(cleanup);

const zones = [
  { id: 'a', label: 'Северный участок', geometry: { type: 'Polygon', coordinates: [] } },
  { id: 'b', label: 'Южный участок', geometry: { type: 'Polygon', coordinates: [] } },
];

describe('PlantingZoneManager', () => {
  it('focuses, renames, redraws and requests deletion without recreating zone controls', () => {
    const onFocus = vi.fn();
    const onRename = vi.fn();
    const onRedraw = vi.fn();
    const onDelete = vi.fn();
    render(<PlantingZoneManager zones={zones} onFocus={onFocus} onRename={onRename} onRedraw={onRedraw} onDelete={onDelete} onDraw={vi.fn()} onCancelDraw={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Показать Северный участок' }));
    fireEvent.click(screen.getByRole('button', { name: 'Перерисовать Северный участок' }));
    fireEvent.click(screen.getByRole('button', { name: 'Удалить Северный участок' }));
    const name = screen.getByRole('textbox', { name: 'Название Северный участок' });
    fireEvent.change(name, { target: { value: 'Главная аллея' } });
    fireEvent.blur(name);

    expect(onFocus).toHaveBeenCalledWith(zones[0]);
    expect(onRedraw).toHaveBeenCalledWith(zones[0]);
    expect(onDelete).toHaveBeenCalledWith(zones[0]);
    expect(onRename).toHaveBeenCalledWith(zones[0], 'Главная аллея');
  });
});
