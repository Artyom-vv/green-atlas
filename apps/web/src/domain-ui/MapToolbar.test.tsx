import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MapToolbar } from './MapToolbar';

describe('MapToolbar', () => {
  afterEach(cleanup);
  const props = { tool: 'select' as const, onTool: vi.fn(), onDelete: vi.fn(), canDelete: false };

  it('keeps the editing surface to placement actions', () => {
    render(<MapToolbar {...props} />);
    expect(screen.getByRole('toolbar', { name: 'Инструменты карты' })).toHaveClass('map-control-group--horizontal');
    expect(screen.getByRole('button', { name: 'Выбрать. Shift — добавить к выбору' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Разместить посадки' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Измерить|Нарисовать участок/ })).not.toBeInTheDocument();
  });

  it('shows bulk deletion only when a map selection exists', () => {
    const onDelete = vi.fn();
    render(<MapToolbar {...props} onDelete={onDelete} canDelete />);
    screen.getByRole('button', { name: 'Удалить выбранное' }).click();
    expect(onDelete).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Разместить посадки' })).toBeEnabled();
  });
});
