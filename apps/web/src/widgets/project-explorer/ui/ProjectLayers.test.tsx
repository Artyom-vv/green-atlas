import type { Layer } from '@green/api-client';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProjectLayers } from './ProjectLayers';

afterEach(cleanup);
const layers: Layer[] = [
  {
    id: 'heat',
    source_name: 'UTIL_HEAT',
    mapped_kind: 'utility',
    suggested_kind: 'utility',
    object_count: 18,
    color: '#ff9900',
    linetype: 'DASHED',
    lineweight_mm: 0.5,
    entity_types: { LINE: 18 },
    geometry_complete: true,
    required: false,
    visible: true,
  },
  {
    id: 'building',
    source_name: 'BLDG_01',
    mapped_kind: 'building',
    suggested_kind: 'building',
    object_count: 4,
    color: '#888888',
    linetype: 'CONTINUOUS',
    lineweight_mm: 0.25,
    entity_types: { LWPOLYLINE: 4 },
    geometry_complete: true,
    required: false,
    visible: true,
  },
];
it('filters by role or source name without changing selection or layer visibility', () => {
  const onSelect = vi.fn(),
    onVisibility = vi.fn();
  render(
    <ProjectLayers
      layers={layers}
      visibility={{ heat: false }}
      activeLayerId="building"
      onSelect={onSelect}
      onVisibility={onVisibility}
    />,
  );
  const search = screen.getByRole('textbox', { name: 'Найти слой' });
  const layer = screen.getByRole('button', { name: /Инженерная сеть/ });
  const listViewport = layer.closest('[data-slot="scroll-viewport"]');
  expect(listViewport).not.toBeNull();
  expect(listViewport).not.toContainElement(search);
  expect(search.closest('[data-slot="scroll-viewport"]')).toBeNull();
  fireEvent.change(search, {
    target: { value: 'инженерная' },
  });
  expect(screen.getByRole('button', { name: /Инженерная сеть/ })).toBeVisible();
  expect(
    screen.queryByRole('button', { name: /Здание/ }),
  ).not.toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();
  expect(onVisibility).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Показать слой' }));
  expect(onVisibility).toHaveBeenCalledExactlyOnceWith('heat', true);
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /Инженерная сеть/ }));
  expect(onSelect).toHaveBeenCalledExactlyOnceWith('heat');
  fireEvent.change(screen.getByRole('textbox', { name: 'Найти слой' }), {
    target: { value: ' BLDG_01 ' },
  });
  expect(screen.getByRole('button', { name: /Здание/ })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});
