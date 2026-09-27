import { MapToolbar } from '@/widgets/map/ui/MapToolbar';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

describe('MapToolbar', () => {
  afterEach(cleanup);
  const props = {
    tool: 'select' as const,
    onTool: vi.fn(),
    onDelete: vi.fn(),
    canDelete: false,
  };

  it('keeps the editing surface to placement actions', () => {
    render(<MapToolbar {...props} />);
    expect(
      screen.getByRole('toolbar', { name: 'Инструменты карты' }),
    ).toHaveAttribute('aria-orientation', 'vertical');
    expect(
      screen.getByRole('button', {
        name: 'Выбрать. Shift — добавить к выбору',
      }),
    ).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Разместить посадки' }),
    ).toBeEnabled();
    expect(
      screen.queryByRole('button', { name: /Измерить|Нарисовать участок/ }),
    ).not.toBeInTheDocument();
  });

  it('shows bulk deletion only when a map selection exists', () => {
    const onDelete = vi.fn();
    render(<MapToolbar {...props} onDelete={onDelete} canDelete />);
    screen.getByRole('button', { name: 'Удалить выбранное' }).click();
    expect(onDelete).toHaveBeenCalledOnce();
    expect(
      screen.getByRole('button', { name: 'Разместить посадки' }),
    ).toBeEnabled();
  });

  it('keeps inactive tools unobtrusive and exposes the active tool separately', () => {
    render(<MapToolbar {...props} />);
    const selected = screen.getByRole('button', {
      name: 'Выбрать. Shift — добавить к выбору',
    });
    const placement = screen.getByRole('button', {
      name: 'Разместить посадки',
    });
    expect(selected).toHaveAttribute('data-variant', 'ghost');
    expect(selected).toHaveAttribute('aria-pressed', 'true');
    expect(placement).toHaveAttribute('data-variant', 'ghost');
    expect(placement).toHaveAttribute('aria-pressed', 'false');
  });
});
