import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
  afterEach(cleanup);
  it('does not suggest a territory when no usable contour exists', () => {
    render(<BoundaryCandidatePicker layers={[layers[0]]} mappings={mappings} onChange={vi.fn()} />);
    expect(screen.queryByLabelText('Контур территории')).toBeNull();
  });

  it('disables boundary changes while preparation is running', () => {
    render(<BoundaryCandidatePicker layers={layers} mappings={mappings} readOnly onChange={vi.fn()} />);
    expect(screen.getByLabelText('Контур территории')).toBeDisabled();
  });

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
      order: {
        ...mappings.order,
        kind: 'site_border',
        category: 'project_boundary',
        confirmed: true,
      },
    });
  });

  it('keeps category and role consistent when switching, clearing and restoring a boundary', () => {
    const onChange = vi.fn();
    const initial: Record<string, LayerMapping> = {
      ...mappings,
      thin: { ...mappings.thin, kind: 'site_border', category: 'project_boundary', confirmed: true },
    };
    const { rerender } = render(<BoundaryCandidatePicker layers={layers} mappings={initial} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Контур территории'), { target: { value: 'order' } });
    const switched = onChange.mock.lastCall![0];
    expect(switched.thin).toEqual({ ...initial.thin, kind: 'ignore', category: 'boundary_decoration' });
    expect(switched.order).toMatchObject({ kind: 'site_border', category: 'project_boundary', confirmed: true });
    expect(initial.thin.kind).toBe('site_border');
    rerender(<BoundaryCandidatePicker layers={layers} mappings={switched} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Контур территории'), { target: { value: '' } });
    const cleared = onChange.mock.lastCall![0];
    expect(Object.values(cleared).some((m) => (m as LayerMapping).kind === 'site_border')).toBe(false);
    expect(cleared.order.category).toBe('boundary_decoration');
    rerender(<BoundaryCandidatePicker layers={layers} mappings={cleared} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Контур территории'), { target: { value: 'order' } });
    expect(onChange.mock.lastCall![0]).toEqual(switched);
  });
});
