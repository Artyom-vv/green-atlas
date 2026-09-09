import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import { PlacementAllocation } from './PlacementAllocation';
import { allocationHint } from './placementAllocationHint';

afterEach(cleanup);
const zones = [{ id: 'a', label: 'Запад', geometry: {} }, { id: 'b', label: 'Восток', geometry: {} }];

describe('placement allocation', () => {
  it('shows each quota and shortfall directly, not in a collapsed disclosure', () => {
    const preview = { requested_count: 4, accepted_count: 2, zone_allocations: [
      { zone_id: 'a', requested_count: 2, accepted_count: 2 },
      { zone_id: 'b', requested_count: 2, accepted_count: 0 },
    ] } as PatternPreview;
    render(<PlacementAllocation zones={zones} selectedIds={['b', 'a']} preview={preview} />);
    const table = screen.getByRole('table', { name: 'Распределение по участкам' });
    expect(within(table).getAllByRole('row').map(row => row.textContent)).toEqual(['УчастокНужноНайдено', 'Запад22', 'Восток20']);
    expect(screen.getByText('Недостающие посадки не перенесены на другие участки.')).toBeVisible();
  });

  it('does not invent per-zone quotas for available capacity or older responses', () => {
    render(<PlacementAllocation zones={zones} selectedIds={['a', 'b']} preview={{ requested_count: 4, accepted_count: 4, change_set: { additions: [{ planting_zone_id: 'a' }, { planting_zone_id: 'a' }, { planting_zone_id: 'a' }, { planting_zone_id: 'a' }] } } as PatternPreview} />);
    expect(screen.queryByRole('columnheader', { name: 'Нужно' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('row').map(row => row.textContent)).toEqual(['УчастокНайдено', 'Запад4', 'Восток0']);
  });

  it('explains total-count remainders before calculation', () => {
    expect(allocationHint(12, 6)).toBe('По 2 на каждый участок');
    expect(allocationHint(5, 2)).toBe('Первый участок: 3. Остальные: по 2.');
    expect(allocationHint(0, 0)).toBe('');
  });
});
