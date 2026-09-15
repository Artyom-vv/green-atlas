import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PlanObject } from '@green/api-client';
import { ControlProvider, Dialog } from '@green/ui';
import { FormProvider } from 'react-hook-form';
import { PlantingLibrary } from '@/features/planting-library/ui/PlantingLibrary';
import { usePlantingLibraryForm } from '../model/usePlantingLibraryForm';
import { PlantingLibraryBody } from './PlantingLibraryBody';
import { PlantingLibraryFooter } from './PlantingLibraryFooter';
afterEach(cleanup);
const objects = [
  { id: 'a', kind: 'tree', pattern_id: 'brush-one', locked: false },
  { id: 'b', kind: 'shrub', pattern_id: 'brush-two', locked: true },
  { id: 'c', kind: 'tree', species_revision_id: 'lime', locked: false },
].map((object) => ({
  x: 0,
  y: 0,
  radius: 1,
  size_class: 'standard',
  spacing_policy: 'balanced',
  status: 'valid',
  ...object,
})) as PlanObject[];
it('filters individual objects and selects only the filtered set', () => {
  const onSelect = vi.fn();
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map([['lime', 'Липа']])}
      onSelect={onSelect}
      onSpecies={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: 'Липа' },
  });
  expect(screen.getByText('Найдено 1 из 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать найденные' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Редактировать на карте' }),
  );
  expect(onSelect).toHaveBeenCalledWith(['c']);
});
it('distinguishes brush groups and gives separate plantings an explicit filter', () => {
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map()}
      onSelect={vi.fn()}
      onSpecies={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Фильтры' }));
  expect(
    screen.getByRole('option', { name: 'Кисть 1 (1)' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('option', { name: 'Кисть 2 (1)' }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Группа посадок'), {
    target: { value: 'individual' },
  });
  expect(screen.getByRole('checkbox', { name: '№ 3 Дерево' })).toBeVisible();
  expect(
    screen.queryByRole('checkbox', { name: '№ 1 Дерево' }),
  ).not.toBeInTheDocument();
});
it('does not silently include locked plantings in a bulk species change', () => {
  const onSpecies = vi.fn();
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map()}
      onSelect={vi.fn()}
      onSpecies={onSpecies}
      initialIds={['a', 'b']}
    />,
  );
  const action = screen.getByRole('button', { name: 'Назначить породу' });
  expect(action).toHaveAccessibleDescription(
    'Порода изменится у 1 из 2. Закреплённые и недоступные посадки пропускаются.',
  );
  fireEvent.click(action);
  expect(onSpecies).toHaveBeenCalledWith(['a']);
});
it('makes selections outside the active filter explicit', () => {
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map([['lime', 'Липа']])}
      onSelect={vi.fn()}
      onSpecies={vi.fn()}
      initialIds={['a']}
    />,
  );
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: 'Липа' },
  });
  expect(screen.getByText('Выбрано: 1, вне фильтра: 1')).toBeVisible();
});

it('keeps combined filters explicit when collapsed and clears each without resetting search or type', () => {
  const onSelect = vi.fn();
  const zoned = objects.map((object) => ({
    ...object,
    planting_zone_id: object.id === 'c' ? 'south' : 'north',
  }));
  const zones = [
    { id: 'north', label: 'Север', geometry: {} },
    { id: 'south', label: 'Юг', geometry: {} },
  ];
  render(
    <PlantingLibrary
      objects={zoned}
      zones={zones}
      names={new Map()}
      onSelect={onSelect}
      onSpecies={vi.fn()}
      initialIds={['b']}
    />,
  );
  const filters = screen.getByRole('button', { name: 'Фильтры' });
  expect(filters).toHaveAttribute('aria-expanded', 'false');
  expect(
    screen.queryByRole('combobox', { name: 'Состояние посадок' }),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'Тип посадок' }), {
    target: { value: 'tree' },
  });
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск посадок' }), {
    target: { value: 'a' },
  });
  fireEvent.click(filters);
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Состояние посадок' }),
    { target: { value: 'unassigned' } },
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Группа посадок' }), {
    target: { value: 'brush-one' },
  });
  fireEvent.change(screen.getByRole('combobox', { name: 'Участок посадок' }), {
    target: { value: 'north' },
  });
  expect(screen.getByText('Найдено 1 из 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Фильтры (3)' }));
  expect(screen.getByRole('button', { name: 'Фильтры (3)' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  expect(
    screen.getByRole('button', { name: 'Сбросить фильтр: Группа: Кисть 1' }),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Сбросить фильтр: Участок: Север' }),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Сбросить фильтр: Без породы' }),
  );
  expect(screen.getByRole('button', { name: 'Фильтры (2)' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  expect(screen.getByRole('textbox', { name: 'Поиск посадок' })).toHaveValue(
    'a',
  );
  expect(screen.getByRole('combobox', { name: 'Тип посадок' })).toHaveValue(
    'tree',
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Редактировать на карте' }),
  );
  expect(onSelect).toHaveBeenCalledWith(['b']);
});

it('unions found IDs with selections outside the filter and avoids duplicates', () => {
  const onSelect = vi.fn();
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map([['lime', 'Липа']])}
      onSelect={onSelect}
      onSpecies={vi.fn()}
      initialIds={['a']}
    />,
  );
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: 'Липа' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать найденные' }));
  expect(
    screen.getByRole('button', { name: 'Выбрать найденные' }),
  ).toBeDisabled();
  expect(screen.getByText('Выбрано: 2, вне фильтра: 1')).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: '№ 3 Дерево' }));
  expect(screen.getByText('Выбрано: 1, вне фильтра: 1')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать найденные' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Редактировать на карте' }),
  );
  expect(onSelect).toHaveBeenCalledWith(['a', 'c']);
  fireEvent.click(screen.getByRole('button', { name: 'Снять выбор' }));
  expect(screen.getByText('Выбрано: 0')).toBeVisible();
});

it('explains a locked-only selection without a misleading zero in the action', () => {
  const onSpecies = vi.fn();
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map()}
      onSelect={vi.fn()}
      onSpecies={onSpecies}
      initialIds={['b']}
    />,
  );
  const action = screen.getByRole('button', { name: 'Назначить породу' });
  expect(action).toBeDisabled();
  expect(action).toHaveAccessibleDescription(
    'Выбранные посадки закреплены. Снимите закрепление на карте, чтобы назначить породу.',
  );
  fireEvent.click(action);
  expect(onSpecies).not.toHaveBeenCalled();
  expect(screen.getByRole('row', { name: /№ 2 Кустарник/ })).toHaveAttribute(
    'data-selected',
    'true',
  );
});

it('keeps locked and unassigned state filters distinct and searchable by original ordinal and ID', () => {
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map()}
      onSelect={vi.fn()}
      onSpecies={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Фильтры' }));
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Состояние посадок' }),
    { target: { value: 'locked' } },
  );
  expect(screen.getByRole('checkbox', { name: '№ 2 Кустарник' })).toBeVisible();
  expect(
    screen.queryByRole('checkbox', { name: '№ 1 Дерево' }),
  ).not.toBeInTheDocument();
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Состояние посадок' }),
    { target: { value: 'unassigned' } },
  );
  expect(screen.getByText('Найдено 2 из 3')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: '2' },
  });
  expect(screen.getByRole('checkbox', { name: '№ 2 Кустарник' })).toBeVisible();
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: 'a' },
  });
  expect(screen.getByRole('checkbox', { name: '№ 1 Дерево' })).toBeVisible();
  expect(
    screen.queryByRole('checkbox', { name: '№ 2 Кустарник' }),
  ).not.toBeInTheDocument();
});

it('keeps selected objects actionable when no filtered results remain', () => {
  const onSelect = vi.fn(),
    onSpecies = vi.fn();
  render(
    <PlantingLibrary
      objects={objects}
      zones={[]}
      names={new Map()}
      onSelect={onSelect}
      onSpecies={onSpecies}
      initialIds={['a']}
    />,
  );
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: 'несуществующее растение' },
  });
  expect(screen.getByText('Найдено 0 из 3')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Выбрать найденные' }),
  ).toBeDisabled();
  expect(screen.getByText('Выбрано: 1, вне фильтра: 1')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Назначить породу' }));
  expect(onSpecies).toHaveBeenCalledWith(['a']);
  fireEvent.click(
    screen.getByRole('button', { name: 'Редактировать на карте' }),
  );
  expect(onSelect).toHaveBeenCalledWith(['a']);
});

it('shares selection with a dialog footer while empty filtering keeps the same table and column headers', () => {
  const onSelect = vi.fn();
  function LibraryDialog() {
    const form = usePlantingLibraryForm(['a']);
    const names = new Map([['lime', 'Липа']]);
    return (
      <FormProvider {...form}>
        <ControlProvider size="compact">
          <Dialog
            open
            stableHeight
            title="Посадки проекта"
            onClose={vi.fn()}
            footer={
              <PlantingLibraryFooter
                objects={objects}
                names={names}
                onSelect={onSelect}
                onSpecies={vi.fn()}
              />
            }
          >
            <PlantingLibraryBody objects={objects} zones={[]} names={names} />
          </Dialog>
        </ControlProvider>
      </FormProvider>
    );
  }
  render(<LibraryDialog />);
  const table = screen.getByRole('table', { name: 'Посадки проекта' });
  const headers = screen.getAllByRole('columnheader');
  const action = screen.getByRole('button', { name: 'Редактировать на карте' });
  const footer = action.closest('footer');
  expect(footer).not.toBeNull();
  expect(footer).not.toContainElement(table);
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: 'не найдено' },
  });
  expect(screen.getByRole('table', { name: 'Посадки проекта' })).toBe(table);
  expect(screen.getAllByRole('columnheader')).toEqual(headers);
  expect(screen.getByRole('status')).toHaveTextContent('Нет посадок');
  expect(screen.getByText('Выбрано: 1, вне фильтра: 1')).toBeVisible();
  fireEvent.click(action);
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(['a']);
  fireEvent.change(screen.getByLabelText('Поиск посадок'), {
    target: { value: '' },
  });
  expect(screen.getByRole('checkbox', { name: '№ 1 Дерево' })).toBeChecked();
  expect(screen.getByRole('table', { name: 'Посадки проекта' })).toBe(table);
});
