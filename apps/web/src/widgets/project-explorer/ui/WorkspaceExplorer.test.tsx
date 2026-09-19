import type { PlanObject } from '@green/api-client';
import { IDEWorkspaceShell } from '@/widgets/workbench';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceExplorer } from './WorkspaceExplorer';
import { ProjectLayers } from './ProjectLayers';
afterEach(cleanup);

const zones = [
  { id: 'west', label: 'Газон', geometry: {} },
  { id: 'east', label: 'Газон', geometry: {} },
];
const objects = Array.from({ length: 6 }, (_, index) => ({
  id: `plant-${index}`,
  kind: index < 4 ? 'tree' : 'shrub',
  species_revision_id: index < 4 ? 'lime' : 'dogwood',
  x: index,
  y: 0,
  radius: 1,
  size_class: 'standard',
  spacing_policy: 'balanced',
  status: 'valid',
  locked: false,
})) as PlanObject[];
function treeProps(
  overrides: Partial<ComponentProps<typeof WorkspaceExplorer>> = {},
): ComponentProps<typeof WorkspaceExplorer> {
  return {
    zones,
    objects,
    selectedIds: ['plant-0'],
    selectedZoneIds: ['west'],
    speciesNames: new Map([
      ['lime', 'Липа'],
      ['dogwood', 'Дёрен'],
    ]),
    layers: <p>Слои чертежа</p>,
    sourceCount: 8,
    onSelect: vi.fn(),
    onZone: vi.fn(),
    onZonesChange: vi.fn(),
    onManageZones: vi.fn(),
    onManagePlantings: vi.fn(),
    onSource: vi.fn(),
    ...overrides,
  };
}

it('follows a layers navigation command and lets the parent select the project tab again', () => {
  const onTabChange = vi.fn();
  const props = treeProps({ activeTab: 'project', onTabChange });
  const { rerender } = render(<WorkspaceExplorer {...props} />);
  rerender(<WorkspaceExplorer {...props} activeTab="layers" />);
  expect(screen.getByText('Слои чертежа')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Проект' }));
  expect(onTabChange).toHaveBeenCalledWith('project');
  rerender(<WorkspaceExplorer {...props} activeTab="project" />);
  expect(
    screen.getByRole('region', { name: 'Рабочие участки проекта' }),
  ).toBeVisible();
});

it('selects separately labelled zones and keeps map focus separate from selection', () => {
  const props = treeProps();
  render(<WorkspaceExplorer {...props} />);
  expect(
    screen.queryByRole('button', { name: /^(Свернуть|Развернуть) проект$/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Газон 1' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Газон 2' }));
  expect(props.onZonesChange).toHaveBeenLastCalledWith(['west', 'east']);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Газон 1' }));
  expect(props.onZonesChange).toHaveBeenLastCalledWith([]);
  fireEvent.click(
    screen.getByRole('button', { name: 'Показать участок: Газон 2' }),
  );
  expect(props.onZone).toHaveBeenCalledExactlyOnceWith(zones[1]);
  expect(props.onSelect).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Управление рабочими участками' }),
  );
  expect(props.onManageZones).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: /^Рабочие участки/ }));
  expect(
    screen.queryByRole('checkbox', { name: 'Газон 1' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Слои' }));
  expect(screen.getByText('Слои чертежа')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Проект' }));
  expect(
    screen.getByRole('button', { name: /^Рабочие участки/ }),
  ).toHaveAttribute('aria-expanded', 'false');
});

it('shows individual plantings by species and limits a large list without hiding its total', () => {
  const props = treeProps();
  const { rerender } = render(<WorkspaceExplorer {...props} />);
  expect(
    screen.getAllByRole('button', { name: /^Выбрать посадку №/ }),
  ).toHaveLength(6);
  expect(screen.getByRole('region', { name: 'Деревья' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Кустарники' })).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Выбрать посадку № 1: Липа' }),
  ).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(
    screen.getByRole('button', { name: 'Выбрать посадку № 6: Дёрен' }),
  );
  expect(props.onSelect).toHaveBeenCalledExactlyOnceWith(['plant-5']);
  const many = Array.from({ length: 61 }, (_, index) => ({
    ...objects[0],
    id: `many-${index}`,
  }));
  rerender(<WorkspaceExplorer {...props} objects={many} />);
  expect(
    screen.getAllByRole('button', { name: /^Выбрать посадку №/ }),
  ).toHaveLength(50);
  expect(screen.getByText('Показано 50 из 61 посадок')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Все посадки (61)' }));
  expect(props.onManagePlantings).toHaveBeenCalledOnce();
});

it('respects planting and zone-selection restrictions independently', () => {
  const props = treeProps({ disabled: true });
  const { rerender } = render(<WorkspaceExplorer {...props} />);
  expect(
    screen.getByRole('button', { name: 'Выбрать посадку № 1: Липа' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Все посадки (6)' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Управление рабочими участками' }),
  ).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'Газон 1' })).toBeEnabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Показать участок: Газон 1' }),
  );
  expect(props.onZone).toHaveBeenCalledExactlyOnceWith(zones[0]);
  rerender(
    <WorkspaceExplorer {...props} disabled={false} zoneSelectionDisabled />,
  );
  expect(screen.getByRole('checkbox', { name: 'Газон 1' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Выбрать посадку № 1: Липа' }),
  ).toBeEnabled();
  rerender(
    <WorkspaceExplorer {...props} disabled={false} onZonesChange={undefined} />,
  );
  expect(screen.getByRole('checkbox', { name: 'Газон 1' })).toBeDisabled();
});

it('opens source settings without changing either selection', () => {
  const props = treeProps();
  render(<WorkspaceExplorer {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Исходные данные' }));
  expect(props.onSource).toHaveBeenCalledOnce();
  expect(props.onSelect).not.toHaveBeenCalled();
  expect(props.onZonesChange).not.toHaveBeenCalled();
});

it('keeps explorer navigation and layer input when its dock is collapsed and resized', () => {
  const props = treeProps({
    layers: (
      <ProjectLayers
        layers={[]}
        visibility={{}}
        onSelect={vi.fn()}
        onVisibility={vi.fn()}
        onGroupVisibility={vi.fn()}
      />
    ),
  });
  render(
    <IDEWorkspaceShell
      header={<span>Проект</span>}
      map={<div>Карта</div>}
      resources={<WorkspaceExplorer {...props} />}
      rightOpen={false}
      inspector={null}
      tool={null}
      assistant={null}
      results={{ checks: null, schedule: null, history: null }}
    />,
  );
  const navigation = screen.getByRole('toolbar', {
    name: 'Обозреватель проекта',
  });
  const host = navigation.parentElement;
  fireEvent.click(screen.getByRole('button', { name: 'Слои' }));
  const search = screen.getByRole('textbox', { name: 'Найти слой' });
  expect(search.closest('[data-slot="scroll-viewport"]')).toBeNull();
  fireEvent.change(search, { target: { value: 'длинное имя слоя' } });
  fireEvent.click(screen.getByRole('button', { name: 'Проект' }));
  expect(search).not.toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Слои' }));
  expect(screen.getByRole('textbox', { name: 'Найти слой' })).toBe(search);
  expect(search).toHaveValue('длинное имя слоя');
  fireEvent.click(
    screen.getByRole('button', { name: 'Свернуть ресурсы проекта' }),
  );
  expect(navigation).not.toBeVisible();
  expect(search).not.toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Показать ресурсы проекта' }),
  );
  fireEvent.keyDown(
    screen.getByRole('separator', { name: 'Ширина ресурсов проекта' }),
    { key: 'Home' },
  );
  expect(navigation.parentElement).toBe(host);
  expect(screen.getByRole('button', { name: 'Слои' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(screen.getByRole('textbox', { name: 'Найти слой' })).toBe(search);
  expect(search).toHaveValue('длинное имя слоя');
});
