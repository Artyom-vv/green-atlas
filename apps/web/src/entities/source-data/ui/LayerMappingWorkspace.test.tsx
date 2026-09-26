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
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск слоя' }), {
    target: { value: 'газон' },
  });
  expect(screen.getByText('Тротуар за Газон')).toBeVisible();
  expect(screen.queryByText('Здание', { selector: 'code' })).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent('Показано 1 из 2');
});

it('keeps confirmed rows and keyboard focus in place until the filter is refreshed', () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole('button', { name: 'Требуют проверки (2)' }));
  const controls = screen.getAllByRole('combobox');
  controls[0].focus();
  fireEvent.keyDown(controls[0], { key: 'ArrowDown', altKey: true });
  expect(controls[1]).toHaveFocus();
  const row = screen.getByText('Тротуар за Газон').closest('li')!;
  fireEvent.click(
    within(row).getByRole('button', { name: 'Подтвердить роль' }),
  );
  expect(screen.getByText('Тротуар за Газон').closest('li')).toBe(row);
  expect(screen.getAllByRole('combobox')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Требуют проверки (1)' }));
  expect(screen.queryByText('Тротуар за Газон')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Все' }));
  expect(screen.getByText('Тротуар за Газон')).toBeVisible();
});
