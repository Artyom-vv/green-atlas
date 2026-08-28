import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ObjectInspector } from './ObjectInspector';

afterEach(cleanup);

describe('ObjectInspector', () => {
  it('keeps a selected planting focused on validation and direct actions', () => {
    render(<ObjectInspector object={{ id: 'plant-1', kind: 'tree', x: 127.41, y: 88.29, radius: 1.6, size_class: 'unspecified', locked: false, status: 'valid' }} onSpecies={vi.fn()} onGrowthHorizon={vi.fn()} onMove={vi.fn()} onDelete={vi.fn()} />);

    expect(screen.getByText('Размещение допустимо')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Переместить' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Удалить' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Назначить породу' })).toBeEnabled();
    expect(screen.queryByText(/Координата|Диаметр кроны|PLAN_OBJECT/)).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
});
