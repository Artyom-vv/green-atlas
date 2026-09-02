import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PlanObject } from '@green/api-client';
import { PlantingsOverviewPanel } from './PlantingsOverviewPanel';

const tree = { id: 'tree-1', kind: 'tree', x: 0, y: 0, radius: 1, group_ids: ['group-1'] } as PlanObject;

describe('PlantingsOverviewPanel', () => {
  it('centres one primary action when the plan is empty', () => {
    const onPlace = vi.fn();
    render(<PlantingsOverviewPanel objects={[]} onPlace={onPlace} onFit={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText('Создайте первую схему')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Показать на карте' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Разместить посадки' }));
    expect(onPlace).toHaveBeenCalledOnce();
  });

  it('keeps the established overview hierarchy for an existing plan', () => {
    const onFit = vi.fn();
    render(<PlantingsOverviewPanel objects={[tree]} onPlace={vi.fn()} onFit={onFit} onClose={vi.fn()} />);
    expect(screen.getByText('Выберите группу или участок')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Показать посадки' }));
    expect(onFit).toHaveBeenCalledOnce();
  });
});
