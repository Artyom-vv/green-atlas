import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Layer, LayerMapping } from '@green/api-client';
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

describe('LayerMappingTable', () => {
  afterEach(cleanup);
  it('keeps a long proposal outside the action label and applies its exact category', () => {
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[layer]}
        mappings={{}}
        onChange={onChange}
        recognition={{
          source_sha256: null,
          provider: 'name-rules-v1',
          status: 'completed',
          processed_count: 1,
          total_count: 1,
          categories: [
            {
              category: 'hard_surface',
              kind: 'restricted',
              label: 'Площадка с твёрдым покрытием',
            },
          ],
          proposals: [
            {
              layer_id: layer.id,
              category: 'hard_surface',
              confidence: 'medium',
              evidence: ['Назначение по имени'],
              unresolved: [],
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText('На чертеже: Площадка с твёрдым покрытием'),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Подтвердить: Техническая / непригодная зона' }),
    );
    expect(onChange.mock.calls[0][0][layer.id]).toMatchObject({
      kind: 'restricted',
      category: 'hard_surface',
      confirmed: true,
    });
  });
  it('keeps an uncertain automatic role pending until the operator confirms it', () => {
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

    expect(screen.getByText('Исходный слой')).toBeInTheDocument();
    expect(
      screen.getByRole('list', { name: 'Слои чертежа' }),
    ).not.toHaveTextContent(/[\u00b7\u2022\u2219\u22c5\u2027]/);
    expect(screen.getByText('Требует решения')).toHaveAttribute(
      'title',
      'Название слоя соответствует роли',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить: Инженерная сеть' }));
    expect(onChange).toHaveBeenCalledWith({
      [layer.id]: { ...mapping, confirmed: true },
    });
  });
  it('exposes an unconfirmed ignore even without an automatic review flag', () => {
    const ignored = {
      ...layer,
      mapped_kind: 'ignore' as const,
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
    expect(screen.queryByRole('combobox')).toBeNull();
    fireEvent.click(screen.getByText('Указать влияние на посадки'));
    expect(screen.getByRole('combobox', { name: /Участие в расчёте/ })).toHaveValue('unassigned');
    fireEvent.change(screen.getByRole('combobox', { name: /Участие в расчёте/ }), {
      target: { value: 'ignore' },
    });
    expect(onChange.mock.calls[0][0][ignored.id]).toMatchObject({
      kind: 'ignore',
      confirmed: true,
    });
  });
  it('uses descriptive geometry rather than silently excluding the category', async () => {
    const ignored = { ...layer, mapped_kind: 'ignore' as const };
    const onChange = vi.fn();
    render(
      <LayerMappingTable
        layers={[ignored]}
        mappings={{}}
        onChange={onChange}
        recognition={{
          source_sha256: null,
          provider: 'name-rules-v1',
          status: 'completed',
          processed_count: 1,
          total_count: 1,
          proposals: [],
          categories: [
            { category: 'terrain_slope', kind: null, label: 'Откос рельефа' },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByText('Указать влияние на посадки'));
    fireEvent.click(screen.getByRole('button', { name: 'Исправить распознанный тип' }));
    const search = screen.getByRole('combobox', { name: /Найти тип объектов/ });
    act(() => search.focus());
    fireEvent.change(search, { target: { value: 'Откос' } });
    expect(await screen.findByRole('option', { name: /Откос рельефа/ })).toBeVisible();
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'Enter' });
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(onChange.mock.calls[0][0][ignored.id]).toMatchObject({
      kind: 'ignore',
      confirmed: false,
      category: 'terrain_slope',
    });
  });
  it('requires an explicit calculation role for a descriptive type', () => {
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
        recognition={{
          source_sha256: null,
          provider: 'name-rules-v1',
          status: 'completed',
          processed_count: 1,
          total_count: 1,
          proposals: [],
          categories: [
            { category: 'terrain_slope', kind: null, label: 'Откос рельефа' },
          ],
        }}
      />,
    );
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByText('Учитывать как')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Указать влияние на посадки'));
    expect(screen.getByRole('combobox', { name: /Участие в расчёте/ })).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Подтвердить роль' }),
    ).toBeNull();
    fireEvent.change(screen.getByRole('combobox', { name: /Участие в расчёте/ }), {
      target: { value: 'restricted' },
    });
    expect(onChange.mock.calls[0][0][slope.id]).toMatchObject({
      kind: 'restricted',
      confirmed: true,
      category: 'terrain_slope',
    });
  });
});
