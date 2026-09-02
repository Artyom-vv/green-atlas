import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LayerInspector } from './LayerInspector';

afterEach(cleanup);

describe('LayerInspector', () => {
  it('shows only the layer role and map actions, not CAD implementation detail', () => {
    render(<LayerInspector layer={{ id: 'layer-1', source_name: 'UTIL_HEAT', suggested_kind: 'utility', mapped_kind: 'utility', object_count: 18, color: '#F97316', linetype: 'DASHED', lineweight_mm: 0.5, entity_types: { LINE: 18 }, geometry_complete: true, required: false, visible: true }} visible onVisibility={vi.fn()} onFit={vi.fn()} />);

    expect(screen.getByRole('heading', { name: 'Инженерные сети', level: 2 })).toBeInTheDocument();
    expect(screen.getByText('18 объектов на исходном чертеже.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Найти на карте' })).toBeEnabled();
    expect(screen.queryByText(/Исходный стиль|Типы DXF|Толщина/)).not.toBeInTheDocument();
  });

  it('confirms when the selected layer is hidden', () => {
    render(<LayerInspector layer={{ id: 'layer-1', source_name: 'BUILDING', suggested_kind: 'building', mapped_kind: 'building', object_count: 12, color: '#94A3B8', linetype: 'CONTINUOUS', lineweight_mm: 0.25, entity_types: { LWPOLYLINE: 12 }, geometry_complete: true, required: false, visible: true }} visible={false} onVisibility={vi.fn()} onFit={vi.fn()} />);

    expect(screen.getByText('Слой скрыт')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Показать слой' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Найти на карте' })).toBeDisabled();
  });
});
