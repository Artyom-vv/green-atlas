import { useState } from 'react';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { LayerMappingTable } from './LayerMappingTable';

const layer: Layer = {
  id: 'network',
  source_name: 'СУЩ_СЕТИ',
  suggested_kind: 'utility',
  suggestion_confidence: 'medium',
  suggestion_reasons: ['Название слоя соответствует роли'],
  mapping_review_required: true,
  mapping_confirmed: false,
  mapped_kind: 'utility',
  object_count: 12,
  color: '#111111',
  linetype: 'CONTINUOUS',
  geometry_complete: true,
  required: false,
  visible: true,
};
const recognition = (
  categories: LayerRecognition['categories'],
  proposals: LayerRecognition['proposals'] = [],
): LayerRecognition => ({
  source_sha256: null,
  provider: 'name-rules-v1',
  status: 'completed',
  stage_processed_count: 0,
  stage_total_count: 0,
  processed_count: 1,
  total_count: 1,
  categories,
  proposals,
});

afterEach(cleanup);

describe('LayerMappingTable', () => {
  it('freezes existing controls during preparation without replacing the row or its draft', () => {
    const onChange = vi.fn();
    const props = { layers: [layer], mappings: {}, onChange };
    const { rerender } = render(<LayerMappingTable {...props} />);
    const role = screen.getByRole('combobox', { name: /Роль в расчёте/ });
    rerender(<LayerMappingTable {...props} disabled />);
    expect(screen.getByRole('combobox', { name: /Роль в расчёте/ })).toBe(role);
    expect(role).toBeDisabled();
    fireEvent.change(role, { target: { value: 'building' } });
    expect(onChange).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: /Сведения и параметры слоя/ }),
    ).toBeEnabled();
    rerender(<LayerMappingTable {...props} />);
    expect(role).toBeEnabled();
    expect(role).toHaveValue('utility');
  });

  it('shows boundary topology intent without demanding or confirming a physical layer role', () => {
    const boundary = {
      ...layer,
      source_name: 'Граница растительности и грунта',
      mapped_kind: 'ignore' as const,
      category: 'surface_boundary' as const,
    };
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[boundary]}
        mappings={{}}
        onChange={onChange}
      />,
    );
    expect(screen.getByText('Граница для замыкания')).toBeVisible();
    expect(screen.getByText('Для подготовки')).toBeVisible();
    expect(
      screen.queryByRole('combobox', { name: /Роль в расчёте/ }),
    ).toBeNull();
    expect(screen.queryByText('Принято')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Сведения о слое/ }));
    expect(screen.queryByText(/расчётная роль не задана/)).toBeNull();
    expect(screen.queryByText('Роль не выбрана')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });
  it('routes an unassigned layer zero to object roles while preserving pending review', () => {
    render(
      <LayerMappingTable
        layers={[{ ...layer, source_name: '0', mapped_kind: 'ignore' }]}
        mappings={{}}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('По объектам')).toBeVisible();
    expect(screen.getByText('Проверить объекты')).toBeVisible();
    expect(
      screen.queryByRole('combobox', { name: /Роль в расчёте/ }),
    ).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Сведения о слое/ }));
    expect(
      screen.getByText('Назначения задаются отдельным объектам'),
    ).toBeVisible();
    expect(screen.queryByText('Принято')).toBeNull();
  });
  it('accepts the proposed role in one action and preserves its exact category', () => {
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[layer]}
        mappings={{}}
        onChange={onChange}
        recognition={recognition(
          [
            {
              category: 'hard_surface',
              kind: 'restricted',
              label: 'Площадка с твёрдым покрытием',
            },
          ],
          [
            {
              layer_id: layer.id,
              category: 'hard_surface',
              confidence: 'medium',
              evidence: ['Назначение по имени'],
              unresolved: [],
            },
          ],
        )}
      />,
    );
    expect(
      screen.getByRole('combobox', { name: /Тип на чертеже/ }),
    ).toHaveValue('hard_surface');
    expect(
      screen.getByRole('combobox', { name: /Роль в расчёте/ }),
    ).toHaveValue('restricted');
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Принять назначение СУЩ_СЕТИ' }),
    );
    expect(onChange.mock.calls[0][0][layer.id]).toMatchObject({
      kind: 'restricted',
      category: 'hard_surface',
      confirmed: true,
    });
  });

  it('keeps a suggested utility role pending until explicit acceptance', () => {
    const mapping: LayerMapping = {
      layer_id: layer.id,
      kind: 'utility',
      confirmed: false,
      visible: true,
    };
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[layer]}
        mappings={{ [layer.id]: mapping }}
        onChange={onChange}
      />,
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(
      screen.getByRole('list', { name: 'Слои чертежа' }),
    ).not.toHaveTextContent(/[\u00b7\u2022\u2219\u22c5\u2027]/);
    expect(screen.queryByRole('combobox', { name: 'Тип сети' })).toBeNull();
    fireEvent.click(
      screen.getByRole('button', { name: 'Принять назначение СУЩ_СЕТИ' }),
    );
    expect(onChange).toHaveBeenCalledWith({
      [layer.id]: { ...mapping, confirmed: true },
    });
  });

  it('requires explicit ignore selection rather than accepting an unspecified exclusion', () => {
    const ignored = {
      ...layer,
      mapped_kind: 'ignore' as const,
      suggested_kind: 'ignore' as const,
      mapping_review_required: false,
    };
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[ignored]}
        mappings={{}}
        onChange={onChange}
      />,
    );
    const role = screen.getByRole('combobox', { name: /Роль в расчёте/ });
    expect(role).toHaveValue('');
    expect(
      screen.queryByRole('button', { name: /Принять назначение/ }),
    ).toBeNull();
    fireEvent.change(role, { target: { value: 'ignore' } });
    expect(onChange.mock.calls[0][0][ignored.id]).toMatchObject({
      kind: 'ignore',
      confirmed: true,
    });
  });

  it('keeps descriptive types unresolved until a calculation role is chosen', () => {
    const ignored = {
      ...layer,
      mapped_kind: 'ignore' as const,
      suggested_kind: 'ignore' as const,
    };
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[ignored]}
        mappings={{}}
        onChange={onChange}
        recognition={recognition([
          { category: 'terrain_slope', kind: null, label: 'Откос рельефа' },
        ])}
      />,
    );
    fireEvent.change(screen.getByRole('combobox', { name: /Тип на чертеже/ }), {
      target: { value: 'terrain_slope' },
    });
    expect(onChange.mock.calls[0][0][ignored.id]).toMatchObject({
      kind: 'ignore',
      confirmed: false,
      category: 'terrain_slope',
    });
  });

  it('preserves a descriptive category when manually assigning its calculation role', () => {
    const slope = {
      ...layer,
      mapped_kind: 'ignore' as const,
      category: 'terrain_slope' as const,
    };
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[slope]}
        mappings={{}}
        onChange={onChange}
        recognition={recognition([
          { category: 'terrain_slope', kind: null, label: 'Откос рельефа' },
        ])}
      />,
    );
    expect(
      screen.queryByRole('button', { name: /Принять назначение/ }),
    ).toBeNull();
    fireEvent.change(screen.getByRole('combobox', { name: /Роль в расчёте/ }), {
      target: { value: 'restricted' },
    });
    expect(onChange.mock.calls[0][0][slope.id]).toMatchObject({
      kind: 'restricted',
      category: 'terrain_slope',
      confirmed: true,
    });
  });

  it('uses an explicit model role hint without turning a descriptive category into ignore', () => {
    const slope = {
      ...layer,
      mapped_kind: 'ignore' as const,
      category: 'terrain_slope' as const,
    };
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[slope]}
        mappings={{}}
        onChange={onChange}
        recognition={recognition(
          [{ category: 'terrain_slope', kind: null, label: 'Откос рельефа' }],
          [
            {
              layer_id: slope.id,
              category: 'terrain_slope',
              calculation_role: 'restricted',
              confidence: 'high',
              evidence: [],
              unresolved: ['Проверить уклон'],
            },
          ],
        )}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Принять назначение/ }));
    expect(onChange.mock.calls[0][0][slope.id]).toMatchObject({
      kind: 'restricted',
      category: 'terrain_slope',
      confirmed: true,
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения о слое СУЩ_СЕТИ' }),
    );
    expect(screen.getByText('Проверить уклон')).toBeVisible();
  });

  it('does not offer direct acceptance for a low confidence proposal', () => {
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[layer]}
        mappings={{}}
        onChange={onChange}
        recognition={recognition(
          [{ category: 'hard_surface', kind: 'restricted', label: 'Площадка' }],
          [
            {
              layer_id: layer.id,
              category: 'hard_surface',
              confidence: 'low',
              evidence: [],
              unresolved: [],
            },
          ],
        )}
      />,
    );
    expect(
      screen.queryByRole('button', { name: /Принять назначение/ }),
    ).toBeNull();
    expect(
      screen.getByRole('combobox', { name: /Роль в расчёте/ }),
    ).toHaveValue('');
  });

  it('routes boundary decisions to geometry instead of assigning an unverified site role', () => {
    const onChange = vi.fn();
    const onBoundaryReview = vi.fn();
    render(
      <LayerMappingTable
        layers={[layer]}
        mappings={{}}
        onChange={onChange}
        onBoundaryReview={onBoundaryReview}
        recognition={recognition(
          [
            {
              category: 'project_boundary',
              kind: 'site_border',
              label: 'Граница проектирования',
            },
          ],
          [
            {
              layer_id: layer.id,
              category: 'project_boundary',
              confidence: 'high',
              evidence: [],
              unresolved: [],
            },
          ],
        )}
      />,
    );
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.getByText('Не выбрана')).toBeVisible();
    expect(screen.queryByText('Граница участка')).toBeNull();
    expect(
      screen.queryByRole('button', { name: /Принять назначение/ }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole('button', {
        name: /Выбрать границу в разделе Геометрия/,
      }),
    );
    expect(onBoundaryReview).toHaveBeenCalledOnce();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('keeps legacy boundary selection available and shows the discarded boundary as excluded', () => {
    const boundaries = ['A', 'B'].map((id) => ({
      ...layer,
      id,
      source_name: `Граница ${id}`,
      suggested_kind: 'site_border' as const,
    }));
    const boundaryRecognition = recognition(
      [
        {
          category: 'project_boundary',
          kind: 'site_border',
          label: 'Граница проектирования',
        },
        {
          category: 'boundary_decoration',
          kind: 'ignore',
          label: 'Оформление границы',
        },
      ],
      boundaries.map((item) => ({
        layer_id: item.id,
        category: 'project_boundary',
        confidence: 'high',
        evidence: [],
        unresolved: [],
      })),
    );
    function Harness() {
      const [mappings, onChange] = useState<Record<string, LayerMapping>>({
        A: {
          layer_id: 'A',
          kind: 'site_border',
          category: 'project_boundary',
          confirmed: true,
          visible: true,
        },
        B: {
          layer_id: 'B',
          kind: 'ignore',
          confirmed: false,
          visible: true,
        },
      });
      return (
        <LayerMappingTable
          layers={boundaries}
          mappings={mappings}
          onChange={onChange}
          recognition={boundaryRecognition}
        />
      );
    }
    render(<Harness />);
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Роль в расчёте Граница B' }),
      { target: { value: 'site_border' } },
    );
    expect(
      screen.getByRole('combobox', { name: 'Роль в расчёте Граница A' }),
    ).toHaveValue('ignore');
    expect(
      screen.getByRole('combobox', { name: 'Тип на чертеже Граница A' }),
    ).toHaveValue('boundary_decoration');
    expect(
      screen.getByRole('combobox', { name: 'Роль в расчёте Граница B' }),
    ).toHaveValue('site_border');
    expect(screen.getAllByText('Принято')).toHaveLength(2);
  });

  it('does not show column headings for an empty result', () => {
    render(<LayerMappingTable layers={[]} mappings={{}} onChange={() => {}} />);
    expect(screen.queryByText('Исходный слой')).toBeNull();
    expect(screen.queryByText('Решение')).toBeNull();
  });

  it('opens utility details only on entering the role and respects a later close', () => {
    const road = {
      ...layer,
      mapped_kind: 'road' as const,
      suggested_kind: 'road' as const,
    };
    function Harness() {
      const [mappings, onChange] = useState<Record<string, LayerMapping>>({});
      return (
        <LayerMappingTable
          layers={[road]}
          mappings={mappings}
          onChange={onChange}
        />
      );
    }
    render(<Harness />);
    const role = screen.getByRole('combobox', { name: /Роль в расчёте/ });
    fireEvent.change(role, { target: { value: 'utility' } });
    expect(screen.getByRole('combobox', { name: 'Тип сети' })).toBeVisible();
    const disclosure = screen.getByRole('button', {
      name: 'Сведения и параметры слоя СУЩ_СЕТИ',
    });
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('combobox', { name: 'Тип сети' })).toBeNull();
    fireEvent.change(role, { target: { value: 'utility' } });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(disclosure);
    expect(
      within(
        screen.getByRole('region', { name: 'Сведения о слое СУЩ_СЕТИ' }),
      ).getByText('Название слоя соответствует роли'),
    ).toBeVisible();
  });

  it('offers details in readonly mode without editable role or acceptance', () => {
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        readOnly
        layers={[layer]}
        mappings={{}}
        onChange={onChange}
      />,
    );
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(
      screen.queryByRole('button', { name: /Принять назначение/ }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Сведения и параметры слоя СУЩ_СЕТИ',
      }),
    );
    expect(screen.getByRole('combobox', { name: 'Тип сети' })).toBeDisabled();
    expect(screen.getByLabelText('Источник сведений')).toHaveAttribute(
      'readonly',
    );
    expect(onChange).not.toHaveBeenCalled();
  });
});
