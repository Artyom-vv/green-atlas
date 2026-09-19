import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Layer, LayerMapping } from '@green/api-client';
import { BoundaryCandidatePicker } from './BoundaryCandidatePicker';

const layers: Layer[] = [
  {
    id: 'thin',
    source_name: 'Граница работ',
    suggested_kind: 'ignore',
    mapped_kind: 'ignore',
    object_count: 28,
    color: '#111111',
    linetype: 'CONTINUOUS',
    geometry_complete: true,
    required: false,
    visible: true,
    boundary_candidate: {
      status: 'thin',
      basis: 'authored_centerline',
      area_m2: 4059,
      inset_1_5m_area_m2: 0,
      component_count: 20,
      issue: 'После внутреннего отступа 1,5 м не остаётся рабочей площади',
    },
  },
  {
    id: 'order',
    source_name: 'Топография$0$Граница заказа',
    suggested_kind: 'ignore',
    mapped_kind: 'ignore',
    object_count: 1,
    color: '#222222',
    linetype: 'CONTINUOUS',
    geometry_complete: true,
    required: false,
    visible: true,
    boundary_candidate: {
      status: 'usable',
      basis: 'polygonized_linework',
      area_m2: 167950,
      inset_1_5m_area_m2: 164247,
      component_count: 1,
    },
  },
];

const mappings: Record<string, LayerMapping> = {
  thin: { layer_id: 'thin', kind: 'ignore', visible: true },
  order: { layer_id: 'order', kind: 'ignore', visible: true },
};

describe('BoundaryCandidatePicker', () => {
  it('offers only usable contours and replaces the previous site mapping', () => {
    const onChange = vi.fn();
    render(
      <BoundaryCandidatePicker
        layers={layers}
        mappings={mappings}
        onChange={onChange}
      />,
    );

    expect(screen.queryByRole('option', { name: /Граница работ/ })).toBeNull();
    fireEvent.change(screen.getByLabelText('Контур территории'), {
      target: { value: 'order' },
    });
    expect(onChange).toHaveBeenCalledWith({
      thin: mappings.thin,
      order: { ...mappings.order, kind: 'site_border' },
    });
  });
});
