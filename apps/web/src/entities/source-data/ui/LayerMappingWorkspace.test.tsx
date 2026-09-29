import { useState } from 'react';
import {
  cleanup,
  fireEvent,
  render,
  screen,
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

it('searches full names without changing mapping decisions', () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole('button', { name: 'Все слои (2)' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск слоя' }), {
    target: { value: 'газон' },
  });
  expect(screen.getByText('Тротуар за Газон')).toBeVisible();
  expect(screen.queryByText('Здание', { selector: 'div' })).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent('Показано 1 из 2');
});

it('exposes group confirmation immediately, without a nested disclosure', () => {
  render(<Harness />);
  const groups = screen.getByRole('region', { name: 'Подтверждение групп' });
  const confirmations = within(groups).getAllByRole('button', {
    name: 'Подтвердить',
  });
  expect(confirmations).toHaveLength(1);
  expect(confirmations[0]).toBeVisible();
  expect(screen.getByText('Группа 1 из 2')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
  expect(screen.getByText('Группа 2 из 2')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
  fireEvent.click(within(groups).getByRole('button', { name: 'Подтвердить' }));
  expect(
    screen.getByRole('button', { name: 'Подтверждение (1)' }),
  ).toBeVisible();
  expect(screen.queryByRole('combobox')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Все слои (2)' }));
  expect(screen.queryByRole('combobox')).toBeNull();
});

it('keeps confirmed rows in place without expanding every classifier', () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole('button', { name: 'Все слои (2)' }));
  const row = screen.getByText('Тротуар за Газон').closest('li')!;
  fireEvent.click(
    within(row).getByRole('button', { name: 'Подтвердить: Дорога / проезд' }),
  );
  expect(screen.getByText('Тротуар за Газон').closest('li')).toBe(row);
  expect(screen.queryByRole('combobox')).toBeNull();
  expect(screen.getByText('Тротуар за Газон')).toBeVisible();
});

it('opens a group in place without navigating away from pending decisions', () => {
  render(<Harness />);
  fireEvent.click(screen.getAllByText('Посмотреть слой и решение')[0]);
  expect(
    screen.getByRole('region', { name: 'Подтверждение групп' }),
  ).toBeVisible();
  expect(screen.queryByRole('combobox')).toBeNull();
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
  expect(screen.queryByText(/Подтверждено/)).toBeNull();
  expect(screen.queryByRole('button', { name: /Подтвердить/ })).toBeNull();
});
