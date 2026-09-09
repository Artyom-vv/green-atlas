import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ObjectInspector } from './ObjectInspector';

afterEach(cleanup);

describe('ObjectInspector', () => {
  it('keeps a selected planting focused on validation and direct actions', () => {
    render(<ObjectInspector object={{ id: 'plant-1', kind: 'tree', x: 127.41, y: 88.29, radius: 1.6, size_class: 'unspecified', spacing_policy: 'balanced', locked: false, status: 'valid' }} onSpecies={vi.fn()} onGrowthHorizon={vi.fn()} onDelete={vi.fn()} />);

    expect(screen.getByRole('heading', { name: 'Дерево', level: 3 })).toBeInTheDocument();
    expect(screen.getByText('Размещение допустимо')).toBeInTheDocument();
    expect(screen.queryByText('Перетащите посадку прямо на карте')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Переместить' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Удалить' })).toHaveClass('ui-button--danger');
    expect(screen.getByRole('button', { name: 'Удалить' }).closest('.editor-actions')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Назначить вид' })).toBeEnabled();
    expect(screen.getByText('Проверка').closest('.editor-panel__content')).toBeInTheDocument();
    expect(screen.queryByText(/Координата|PLAN_OBJECT/)).not.toBeInTheDocument();
    expect(screen.queryByText('Диаметр кроны')).not.toBeInTheDocument();
    expect(screen.getByText(/Без породы: 1 из 1/)).toBeVisible();
    expect(screen.getByRole('slider', { name: 'Горизонт прогноза' })).toBeEnabled();
  });

  it('does not render a fake disabled footer action in read-only mode', () => {
    render(<ObjectInspector object={{ id: 'plant-1', kind: 'tree', x: 127.41, y: 88.29, radius: 1.6, size_class: 'unspecified', spacing_policy: 'balanced', locked: false, status: 'valid' }} editable={false} onSpecies={vi.fn()} onGrowthHorizon={vi.fn()} onDelete={vi.fn()} />);

    expect(screen.queryByRole('button', { name: 'Удалить' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Зафиксировано/)).not.toBeInTheDocument();
    expect(document.querySelector('.inspector-footer')).not.toBeInTheDocument();
  });
});
