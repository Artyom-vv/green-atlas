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
    render(
      <BoundaryCandidatePicker
        layers={[layers[0]]}
        mappings={mappings}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.queryByRole('group', { name: 'Граница территории' }),
    ).toBeNull();
  });

  it('disables boundary changes while preparation is running', () => {
    render(
      <BoundaryCandidatePicker
        layers={layers}
        mappings={mappings}
        readOnly
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Граница заказа/ }),
    ).toBeDisabled();
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

    expect(screen.queryByRole('button', { name: /Граница работ/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Граница заказа/ }));
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

  it('keeps category and role consistent when switching a boundary', () => {
    const onChange = vi.fn();
    const initial: Record<string, LayerMapping> = {
      ...mappings,
      thin: {
        ...mappings.thin,
        kind: 'site_border',
        category: 'project_boundary',
        confirmed: true,
      },
    };
    const { rerender } = render(
      <BoundaryCandidatePicker
        layers={layers}
        mappings={initial}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Граница заказа/ }));
    const switched = onChange.mock.lastCall![0];
    expect(switched.thin).toEqual({
      ...initial.thin,
      kind: 'ignore',
      category: 'boundary_decoration',
    });
    expect(switched.order).toMatchObject({
      kind: 'site_border',
      category: 'project_boundary',
      confirmed: true,
    });
    expect(initial.thin.kind).toBe('site_border');
    rerender(
      <BoundaryCandidatePicker
        layers={layers}
        mappings={switched}
        onChange={onChange}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Граница заказа/ }),
    ).toHaveAttribute('aria-pressed', 'true');
  });

  it('explains why a named alternative work boundary is not offered', () => {
    const valid = {
      ...layers[1], id: 'valid', source_name: 'Генплан|Граница работ',
    };
    const invalid = {
      ...layers[0], id: 'invalid', source_name: 'Генплан|Граница проектирования',
      boundary_candidate: {
        status: 'unavailable' as const, basis: 'polygonized_linework',
        area_m2: 0, inset_1_5m_area_m2: 0, component_count: 0,
        issue: 'Нет замкнутой площади',
      },
    };
    render(
      <BoundaryCandidatePicker
        layers={[valid, invalid]}
        mappings={mappings}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/Граница проектирования \(Нет замкнутой площади\)/))
      .toBeVisible();
    expect(screen.queryByRole('button', { name: /Граница проектирования/ }))
      .toBeNull();
  });

  it('explains an existing invalid boundary and offers the usable replacement', () => {
    render(
      <BoundaryCandidatePicker
        layers={layers}
        mappings={{
          ...mappings,
          thin: { ...mappings.thin, kind: 'site_border', confirmed: true },
        }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'После внутреннего отступа',
    );
    expect(screen.getByRole('button', { name: /Граница заказа/ }))
      .toHaveAttribute('aria-pressed', 'false');
  });

  it('lets the operator remove an invalid boundary when no usable contour exists', () => {
    const onChange = vi.fn();
    render(
      <BoundaryCandidatePicker
        layers={[layers[0]]}
        mappings={{
          thin: { ...mappings.thin, kind: 'site_border', confirmed: true },
        }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Не использовать эту границу' }));
    expect(onChange).toHaveBeenCalledWith({
      thin: expect.objectContaining({ kind: 'ignore', confirmed: true }),
    });
  });
});
