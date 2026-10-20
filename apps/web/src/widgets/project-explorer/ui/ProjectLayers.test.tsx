import type { Layer } from '@green/api-client';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
      onGroupVisibility={vi.fn()}
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

it('toggles coarse source groups without changing individual selection', () => {
  const projectLayer: Layer = {
    ...layers[1],
    id: 'dendro',
    source_name: '!!!_1. Дендра_сохранить',
  };
  const baseLayer: Layer = {
    ...layers[0],
    id: 'topography',
    source_name: 'ЮАО$0$00.1_Топография$0$Рельеф',
  };
  const onGroupVisibility = vi.fn();
  render(
    <ProjectLayers
      layers={[projectLayer, baseLayer]}
      visibility={{}}
      onVisibility={vi.fn()}
      onGroupVisibility={onGroupVisibility}
      onSelect={vi.fn()}
    />,
  );
  expect(screen.getByText('Проектные слои')).toBeVisible();
  expect(screen.getByText('Геоподоснова')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Скрыть всю группу: Геоподоснова' }),
  );
  expect(onGroupVisibility).toHaveBeenCalledExactlyOnceWith(
    ['topography'],
    false,
  );
});

it('keeps group collapse while search temporarily exposes matches, and clears on Escape', () => {
  const onSelect = vi.fn();
  const onVisibility = vi.fn();
  render(
    <ProjectLayers
      layers={layers}
      visibility={{ heat: false }}
      activeLayerId="heat"
      onSelect={onSelect}
      onVisibility={onVisibility}
      onGroupVisibility={vi.fn()}
    />,
  );
  const group = screen.getByRole('button', { name: /^Прочие слои/ });
  fireEvent.click(group);
  expect(group).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: /Инженерная сеть/ })).toBeNull();
  const search = screen.getByRole('textbox', { name: 'Найти слой' });
  fireEvent.change(search, { target: { value: 'heat' } });
  expect(group).toHaveAttribute('aria-expanded', 'true');
  expect(
    screen.getByRole('button', { name: /Инженерная сеть/ }),
  ).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(group);
  expect(group).toHaveAttribute('aria-expanded', 'false');
  fireEvent.change(search, { target: { value: 'bldg' } });
  expect(group).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('button', { name: /Здание/ })).toBeVisible();
  fireEvent.keyDown(search, { key: 'Escape' });
  expect(search).toHaveValue('');
  expect(group).toHaveAttribute('aria-expanded', 'false');
  expect(onSelect).not.toHaveBeenCalled();
  expect(onVisibility).not.toHaveBeenCalled();
});

it('toggles the whole group from a filtered view and keeps selection independent', () => {
  const onSelect = vi.fn();
  const onGroupVisibility = vi.fn();
  render(
    <ProjectLayers
      layers={layers}
      visibility={{ heat: false }}
      activeLayerId="heat"
      onSelect={onSelect}
      onVisibility={vi.fn()}
      onGroupVisibility={onGroupVisibility}
    />,
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Найти слой' }), {
    target: { value: 'heat' },
  });
  const toggle = screen.getByRole('button', {
    name: 'Скрыть всю группу: Прочие слои (включая слои вне поиска)',
  });
  expect(toggle).toHaveAttribute('aria-pressed', 'mixed');
  fireEvent.click(toggle);
  expect(onGroupVisibility).toHaveBeenCalledExactlyOnceWith(
    ['heat', 'building'],
    false,
  );
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Очистить поиск' }));
  const search = screen.getByRole('textbox', { name: 'Найти слой' });
  expect(search).toHaveValue('');
  expect(search).toHaveFocus();
  expect(
    screen.getByRole('button', { name: /Инженерная сеть/ }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: /Здание/ })).toBeVisible();
  expect(document.querySelector('button button')).toBeNull();
});

it('distinguishes an empty source from a search without matches', () => {
  const props = {
    layers: [],
    visibility: {},
    onSelect: vi.fn(),
    onVisibility: vi.fn(),
    onGroupVisibility: vi.fn(),
  };
  const { rerender } = render(<ProjectLayers {...props} />);
  expect(screen.getByText('Слоёв пока нет')).toBeVisible();
  rerender(<ProjectLayers {...props} layers={layers} />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Найти слой' }), {
    target: { value: 'no such source' },
  });
  expect(screen.getByText('Совпадений нет')).toBeVisible();
  expect(screen.queryByText('Слоёв пока нет')).toBeNull();
});

it('exposes the full source identity through keyboard focus on a layer', async () => {
  const user = userEvent.setup();
  const fullName = 'Ссылка_Топография|UTIL_HEAT';
  render(
    <ProjectLayers
      layers={[{ ...layers[0], source_name: fullName }]}
      visibility={{}}
      onSelect={vi.fn()}
      onVisibility={vi.fn()}
      onGroupVisibility={vi.fn()}
    />,
  );
  await user.tab();
  expect(screen.getByRole('textbox', { name: 'Найти слой' })).toHaveFocus();
  await user.tab();
  await user.tab();
  await user.tab();
  expect(screen.getByRole('button', { name: /Инженерная сеть/ })).toHaveFocus();
  expect(await screen.findByRole('tooltip', { name: fullName })).toBeVisible();
});
