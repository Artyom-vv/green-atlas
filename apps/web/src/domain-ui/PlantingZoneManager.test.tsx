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

    fireEvent.click(screen.getByRole('button', { name: 'Показать участок 1: Северный участок' }));
    fireEvent.click(screen.getByRole('button', { name: 'Перерисовать участок 1: Северный участок' }));
    fireEvent.click(screen.getByRole('button', { name: 'Удалить участок 1: Северный участок' }));
    const name = screen.getByRole('textbox', { name: 'Название участка 1: Северный участок' });
    fireEvent.change(name, { target: { value: 'Главная аллея' } });
    fireEvent.blur(name);

    expect(onFocus).toHaveBeenCalledWith(zones[0]);
    expect(onRedraw).toHaveBeenCalledWith(zones[0]);
    expect(onDelete).toHaveBeenCalledWith(zones[0]);
    expect(onRename).toHaveBeenCalledWith(zones[0], 'Главная аллея');
  });

  it('keeps deletion reason out of the row until the disabled action is focused', () => {
    render(<PlantingZoneManager zones={zones} zoneUsage={{ a: 3 }} onFocus={vi.fn()} onRename={vi.fn()} onRedraw={vi.fn()} onDelete={vi.fn()} onDraw={vi.fn()} onCancelDraw={vi.fn()} />);

    expect(screen.queryByText('Сначала перенесите или удалите 3 посадки')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Удалить участок 1: Северный участок' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Удалить участок 2: Южный участок' })).toBeEnabled();
  });

  it('requires a replacement before deleting the only area', () => {
    render(<PlantingZoneManager zones={[zones[0]]} onFocus={vi.fn()} onRename={vi.fn()} onRedraw={vi.fn()} onDelete={vi.fn()} onDraw={vi.fn()} onCancelDraw={vi.fn()} />);

    expect(screen.queryByText('Сначала создайте другой рабочий участок')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Удалить участок 1: Северный участок' })).toBeDisabled();
  });
});
