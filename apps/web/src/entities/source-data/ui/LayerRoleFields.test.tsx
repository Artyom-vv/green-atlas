import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { LayerRoleFields } from './LayerRoleFields';

afterEach(cleanup);

it('does not confirm a descriptive category as a generic obstacle', async () => {
  const layer = {
    id: 'edge', source_name: 'Граница растительности и грунта',
    mapped_kind: 'ignore', suggested_kind: 'ignore', object_count: 1,
    color: '#333', linetype: 'CONTINUOUS', geometry_complete: true,
    required: false, visible: true, mapping_confirmed: false,
  } as Layer;
  const mapping: LayerMapping = {
    layer_id: layer.id, kind: 'ignore', confirmed: false, visible: true,
  };
  const recognition: LayerRecognition = {
    source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
    processed_count: 1, total_count: 1,
    categories: [{ category: 'surface_boundary', kind: null, label: 'Граница растительности и грунта' }],
    proposals: [{ layer_id: layer.id, category: 'surface_boundary', confidence: 'high', evidence: [], unresolved: [] }],
  };
  const onChange = vi.fn();
  render(<LayerRoleFields layer={layer} mapping={mapping} recognition={recognition} onChange={onChange} />);
  expect(screen.queryByRole('button', { name: 'Принять предложение' })).toBeNull();
  fireEvent.click(screen.getByText('Указать влияние на посадки'));
  fireEvent.click(screen.getByRole('button', { name: 'Исправить распознанный тип' }));
  const search = screen.getByRole('combobox', { name: /Найти тип объектов/ });
  act(() => search.focus());
  fireEvent.change(search, { target: { value: 'растительности' } });
  expect(await screen.findByRole('option', { name: /Граница растительности и грунта/ })).toBeVisible();
  fireEvent.keyDown(search, { key: 'ArrowDown' });
  fireEvent.keyDown(search, { key: 'Enter' });
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
    category: 'surface_boundary', confirmed: false,
  })));
});

it('does not offer the site role for a contour without usable area', () => {
  const layer = {
    id: 'open-line', source_name: 'Граница проектирования',
    mapped_kind: 'ignore', suggested_kind: 'ignore', object_count: 1,
    color: '#333', linetype: 'CONTINUOUS', geometry_complete: true,
    required: false, visible: true, mapping_confirmed: false,
    boundary_candidate: {
      status: 'unavailable', basis: 'polygonized_linework', area_m2: 0,
      inset_1_5m_area_m2: 0, component_count: 0,
      issue: 'Слой не образует замкнутую поверхность',
    },
  } as Layer;
  const mapping: LayerMapping = {
    layer_id: layer.id, kind: 'ignore', confirmed: false, visible: true,
  };
  const recognition: LayerRecognition = {
    source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
    processed_count: 1, total_count: 1,
    categories: [{ category: 'project_boundary', kind: 'site_border', label: 'Граница проектирования' }],
    proposals: [{ layer_id: layer.id, category: 'project_boundary', confidence: 'high', evidence: [], unresolved: [] }],
  };
  render(<LayerRoleFields layer={layer} mapping={mapping} recognition={recognition} onChange={vi.fn()} />);
  expect(screen.queryByRole('option', { name: 'Граница участка' })).toBeNull();
  fireEvent.click(screen.getByText('Указать влияние на посадки'));
  fireEvent.click(screen.getByRole('button', { name: 'Исправить распознанный тип' }));
  const search = screen.getByRole('combobox', { name: /Найти тип объектов/ });
  act(() => search.focus());
  expect(screen.queryByRole('option', { name: 'Граница проектирования' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Принять предложение' })).toBeNull();
});

it('offers one action for a model role hint but does not confirm it on render', () => {
  const layer = {
    id: 'slope', source_name: 'Откос рельефа',
    mapped_kind: 'ignore', suggested_kind: 'ignore', object_count: 4,
    color: '#777', linetype: 'CONTINUOUS', geometry_complete: true,
    required: false, visible: true, mapping_confirmed: false,
  } as Layer;
  const mapping: LayerMapping = {
    layer_id: 'slope', kind: 'ignore', confirmed: false, visible: true,
  };
  const recognition: LayerRecognition = {
    source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
    processed_count: 1, total_count: 1,
    categories: [{ category: 'terrain_slope', kind: null, label: 'Откос рельефа' }],
    proposals: [{ layer_id: 'slope', category: 'terrain_slope',
      calculation_role: 'restricted', confidence: 'high',
      evidence: ['Объекты откоса'], unresolved: [] }],
  };
  const onChange = vi.fn();
  render(<LayerRoleFields layer={layer} mapping={mapping} recognition={recognition} onChange={onChange} />);
  expect(screen.getByText('На чертеже: Откос рельефа')).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Принять роль: Техническая / непригодная зона' }));
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
    kind: 'restricted', category: 'terrain_slope', confirmed: true,
  }));
});

it('explains that a mixed description is not a calculation decision', () => {
  const layer = {
    id: 'mixed', source_name: '0', mapped_kind: 'ignore',
    suggested_kind: 'ignore', object_count: 727, color: '#555',
    linetype: 'CONTINUOUS', geometry_complete: true, required: false,
    visible: true, mapping_confirmed: false,
  } as Layer;
  const mapping: LayerMapping = {
    layer_id: 'mixed', kind: 'ignore', category: 'mixed_source',
    confirmed: false, visible: true,
  };
  const recognition: LayerRecognition = {
    source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
    processed_count: 1, total_count: 1,
    categories: [{ category: 'mixed_source', kind: null, label: 'Смешанный слой без назначения' }],
    proposals: [{ layer_id: 'mixed', category: 'mixed_source', confidence: 'high',
      evidence: [], unresolved: [] }],
  };
  const onChange = vi.fn();
  render(<LayerRoleFields layer={layer} mapping={mapping} recognition={recognition} onChange={onChange} />);
  expect(screen.getByRole('status')).toHaveTextContent('Тип уже распознан');
  expect(screen.getByRole('status')).toHaveTextContent('остаётся на проверке');
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Указать влияние на посадки'));
  expect(screen.getByText(/применяется ко всем 727 объектам слоя/)).toBeVisible();
  expect(screen.queryByRole('combobox', { name: /Найти тип объектов/ })).toBeNull();
  fireEvent.change(screen.getByRole('combobox', { name: /Участие в расчёте/ }), {
    target: { value: 'restricted' },
  });
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
    kind: 'restricted', category: 'mixed_source', confirmed: true,
  }));
});
