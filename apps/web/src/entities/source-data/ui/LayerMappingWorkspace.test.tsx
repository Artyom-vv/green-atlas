import { useState } from 'react';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { Layer, LayerMapping } from '@green/api-client';
import { LayerMappingWorkspace } from './LayerMappingWorkspace';

afterEach(cleanup);
const layers = ['Тротуар за Газон', 'Здание'].map((name, index) => ({
  id: String(index),
  source_name: name,
  mapped_kind: index ? 'building' : 'road',
  suggested_kind: index ? 'building' : 'road',
  mapping_confirmed: false,
  object_count: 1,
  color: '#333333',
  linetype: 'CONTINUOUS',
  geometry_complete: true,
  required: false,
  visible: true,
})) as Layer[];

function Harness() {
  const [mappings, onChange] = useState<Record<string, LayerMapping>>(
    Object.fromEntries(
      layers.map((layer) => [
        layer.id,
        {
          layer_id: layer.id,
          kind: layer.mapped_kind!,
          confirmed: false,
          visible: true,
        },
      ]),
    ),
  );
  return (
    <LayerMappingWorkspace
      layers={layers}
      mappings={mappings}
      onChange={onChange}
    />
  );
}

it('keeps topology intent separate from accepted roles and still reviews unresolved zero objects', async () => {
  const boundary = {
    ...layers[0],
    id: 'boundary',
    source_name: 'Граница улицы',
    mapped_kind: 'ignore' as const,
    category: 'surface_boundary' as const,
  };
  const zero = {
    ...layers[1],
    id: 'zero',
    source_name: '0',
    mapped_kind: 'ignore' as const,
  };
  render(
    <LayerMappingWorkspace
      layers={[boundary, zero]}
      mappings={{}}
      onChange={() => {}}
    />,
  );
  expect(screen.getByRole('tab', { name: 'Проверить (1)' })).toBeVisible();
  expect(screen.getByText('Проверить объекты')).toBeVisible();
  expect(screen.queryByText('Граница улицы')).toBeNull();
  fireEvent.click(screen.getByRole('tab', { name: 'Все слои (2)' }));
  expect(screen.getByText('Граница для замыкания')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'О распознавании слоёв' }),
  );
  const summary = screen.getByRole('dialog', { name: 'Распознавание слоёв' });
  await waitFor(() =>
    expect(within(summary).getByText('Для замыкания')).toBeVisible(),
  );
  expect(within(summary).getByText('0 из 2')).toBeVisible();
});

it('searches full names in the shared table without changing decisions', () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole('tab', { name: 'Все слои (2)' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск слоя' }), {
    target: { value: 'газон' },
  });
  expect(screen.getByText('Тротуар за Газон')).toBeVisible();
  expect(screen.queryByText('Здание', { selector: 'div' })).toBeNull();
  expect(screen.getByRole('tab', { name: 'Проверить (2)' })).toBeVisible();
});

it('uses one table for review and all, retaining accepted rows until reentering review', () => {
  render(<Harness />);
  const row = screen.getByText('Тротуар за Газон').closest('li')!;
  expect(
    within(screen.getByRole('list', { name: 'Слои чертежа' })).getAllByRole(
      'button',
      { name: /Принять назначение/ },
    ),
  ).toHaveLength(2);
  fireEvent.click(
    within(row).getByRole('button', {
      name: 'Принять назначение Тротуар за Газон',
    }),
  );
  expect(screen.getByText('Тротуар за Газон').closest('li')).toBe(row);
  expect(within(row).getByText('Принято')).toBeVisible();
  expect(screen.getByRole('tab', { name: 'Проверить (1)' })).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Все слои (2)' }));
  expect(screen.getByText('Тротуар за Газон').closest('li')).toBe(row);
  fireEvent.click(screen.getByRole('tab', { name: 'Проверить (1)' }));
  expect(screen.queryByText('Тротуар за Газон')).toBeNull();
  expect(screen.getByText('Здание', { selector: 'div' })).toBeVisible();
});

it('keeps the row and its controls in place when a disclosure is opened', () => {
  render(<Harness />);
  const row = screen.getByText('Тротуар за Газон').closest('li')!;
  fireEvent.click(
    within(row).getByRole('button', {
      name: 'Сведения о слое Тротуар за Газон',
    }),
  );
  expect(screen.getByText('Тротуар за Газон').closest('li')).toBe(row);
  expect(
    within(row).getByRole('combobox', {
      name: 'Роль в расчёте Тротуар за Газон',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('region', { name: 'Сведения о слое Тротуар за Газон' }),
  ).toBeVisible();
});

it('keeps secondary counters in the information popover', async () => {
  render(<Harness />);
  expect(screen.queryByText('Обработано')).toBeNull();
  expect(screen.queryByText('Принято')).toBeNull();
  fireEvent.click(
    screen.getByRole('button', { name: 'О распознавании слоёв' }),
  );
  const popover = screen.getByRole('dialog', { name: 'Распознавание слоёв' });
  await waitFor(() =>
    expect(within(popover).getByText('Принято')).toBeVisible(),
  );
  expect(popover).toHaveTextContent('0 из 2');
});

it('omits the fallback information icon when the heading owns it', () => {
  render(
    <LayerMappingWorkspace
      informationInHeader
      layers={layers}
      mappings={{}}
      onChange={() => {}}
    />,
  );
  expect(
    screen.queryByRole('button', { name: 'О распознавании слоёв' }),
  ).toBeNull();
});

it('supports readonly search and details without mutation actions', () => {
  render(
    <LayerMappingWorkspace
      readOnly
      layers={layers}
      mappings={{}}
      onChange={() => {}}
    />,
  );
  expect(screen.getByRole('tab', { name: 'Все слои (2)' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(
    screen.queryByRole('button', { name: /Принять назначение/ }),
  ).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск слоя' }), {
    target: { value: 'газон' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Сведения о слое Тротуар за Газон' }),
  );
  expect(
    screen.getByRole('region', { name: 'Сведения о слое Тротуар за Газон' }),
  ).toBeVisible();
});

it('keeps the writable review view through a temporary readonly status check', () => {
  const props = {
    layers,
    mappings: {},
    onChange: () => {},
    defaultView: 'review' as const,
  };
  const { rerender } = render(<LayerMappingWorkspace {...props} readOnly />);
  expect(screen.getByRole('tab', { name: 'Проверить (2)' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(screen.queryByRole('textbox', { name: 'Поиск слоя' })).toBeNull();
  rerender(<LayerMappingWorkspace {...props} readOnly={false} />);
  expect(screen.getByRole('tab', { name: 'Проверить (2)' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(
    screen.getAllByRole('button', { name: /Принять назначение/ }),
  ).toHaveLength(2);
});

it('does not show zero accepted layers while automatic decisions are being saved', () => {
  render(
    <LayerMappingWorkspace
      automaticAcceptancePending
      layers={layers}
      mappings={{}}
      onChange={() => {}}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'Принимаем однозначные назначения слоёв',
  );
  expect(screen.queryByText(/Принято/)).toBeNull();
  expect(
    screen.queryByRole('button', { name: /Принять назначение/ }),
  ).toBeNull();
});

it('starts the first review with only pending rows after automatic acceptance completes', () => {
  const props = { layers, mappings: {}, onChange: () => {} };
  const { rerender } = render(
    <LayerMappingWorkspace {...props} automaticAcceptancePending />,
  );
  rerender(
    <LayerMappingWorkspace
      {...props}
      automaticAcceptancePending={false}
      mappings={{
        '0': {
          layer_id: '0',
          kind: 'road',
          confirmed: true,
          visible: true,
        },
      }}
    />,
  );
  expect(screen.getByRole('tab', { name: 'Проверить (1)' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(screen.queryByText('Тротуар за Газон')).toBeNull();
  expect(screen.getByText('Здание', { selector: 'div' })).toBeVisible();
});
