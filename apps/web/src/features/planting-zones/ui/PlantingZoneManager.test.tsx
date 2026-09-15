import userEvent from '@testing-library/user-event';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dialog } from '@green/ui';
import { PlantingZoneManager } from '@/features/planting-zones/ui/PlantingZoneManager';

afterEach(cleanup);

async function tabTo(
  user: ReturnType<typeof userEvent.setup>,
  target: HTMLElement,
) {
  for (
    let index = 0;
    index < 30 && document.activeElement !== target;
    index += 1
  ) {
    await user.tab();
  }
  expect(target).toHaveFocus();
}

const zones = [
  {
    id: 'a',
    label: 'Северный участок',
    geometry: { type: 'Polygon', coordinates: [] },
  },
  {
    id: 'b',
    label: 'Южный участок',
    geometry: { type: 'Polygon', coordinates: [] },
  },
];

describe('PlantingZoneManager', () => {
  it('preserves an active rename draft across zone updates and cancels it without closing the dialog', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onRename = vi.fn();
    const props = {
      onFocus: vi.fn(),
      onRename,
      onRedraw: vi.fn(),
      onDelete: vi.fn(),
      onDraw: vi.fn(),
      onCancelDraw: vi.fn(),
    };
    const view = render(
      <Dialog open title="Рабочие участки" onClose={onClose}>
        <PlantingZoneManager zones={zones} {...props} />
      </Dialog>,
    );
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    await user.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Северный участок',
      }),
    );
    await user.click(
      await screen.findByRole('menuitem', { name: 'Переименовать' }),
    );
    const input = screen.getByRole('textbox', {
      name: 'Название участка 1: Северный участок',
    });
    await user.clear(input);
    await user.type(input, '  Рабочее название  ');
    const updatedZones = [
      { ...zones[0], label: 'Название из проекта' },
      zones[1],
    ];
    view.rerender(
      <Dialog open title="Рабочие участки" onClose={onClose}>
        <PlantingZoneManager zones={updatedZones} {...props} />
      </Dialog>,
    );
    expect(
      screen.getByRole('textbox', {
        name: 'Название участка 1: Название из проекта',
      }),
    ).toHaveValue('  Рабочее название  ');
    await user.keyboard('{Escape}');
    expect(onRename).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('textbox', { name: /Название участка/ }),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Название из проекта',
      }),
    );
    await user.click(
      await screen.findByRole('menuitem', { name: 'Переименовать' }),
    );
    const reopened = screen.getByRole('textbox', {
      name: 'Название участка 1: Название из проекта',
    });
    expect(reopened).toHaveValue('Название из проекта');
    await user.clear(reopened);
    await user.type(reopened, '  Новый участок  {Enter}');
    expect(onRename).toHaveBeenCalledExactlyOnceWith(
      updatedZones[0],
      'Новый участок',
    );
  });

  it('focuses, renames, redraws and requests deletion without recreating zone controls', () => {
    const onFocus = vi.fn();
    const onRename = vi.fn();
    const onRedraw = vi.fn();
    const onDelete = vi.fn();
    render(
      <PlantingZoneManager
        zones={zones}
        onFocus={onFocus}
        onRename={onRename}
        onRedraw={onRedraw}
        onDelete={onDelete}
        onDraw={vi.fn()}
        onCancelDraw={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Показать участок 1: Северный участок',
      }),
    );
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Северный участок',
      }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Изменить контур' }));
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Северный участок',
      }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Удалить участок' }));
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Северный участок',
      }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Переименовать' }));
    const name = screen.getByRole('textbox', {
      name: 'Название участка 1: Северный участок',
    });
    fireEvent.change(name, { target: { value: 'Главная аллея' } });
    fireEvent.blur(name);

    expect(onFocus).toHaveBeenCalledWith(zones[0]);
    expect(onRedraw).toHaveBeenCalledWith(zones[0]);
    expect(onDelete).toHaveBeenCalledWith(zones[0]);
    expect(onRename).toHaveBeenCalledWith(zones[0], 'Главная аллея');
  });

  it('explains unavailable deletion in the menu and prevents activation', () => {
    const onDelete = vi.fn();
    render(
      <PlantingZoneManager
        zones={zones}
        zoneUsage={{ a: 3 }}
        onFocus={vi.fn()}
        onRename={vi.fn()}
        onRedraw={vi.fn()}
        onDelete={onDelete}
        onDraw={vi.fn()}
        onCancelDraw={vi.fn()}
      />,
    );

    expect(
      screen.queryByText('Сначала перенесите или удалите 3 посадки'),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Северный участок',
      }),
    );
    const unavailable = screen.getByRole('menuitem', {
      name: 'Удалить участок',
    });
    expect(unavailable).toHaveAttribute('aria-disabled', 'true');
    expect(unavailable).toHaveAccessibleDescription(
      'Сначала перенесите или удалите 3 посадки',
    );
    fireEvent.click(unavailable);
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.keyDown(unavailable, { key: 'Escape' });
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Действия с участком 2: Южный участок',
      }),
    );
    expect(
      screen.getByRole('menuitem', { name: 'Удалить участок' }),
    ).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('requires a replacement before deleting the only area', () => {
    render(
      <PlantingZoneManager
        zones={[zones[0]]}
        onFocus={vi.fn()}
        onRename={vi.fn()}
        onRedraw={vi.fn()}
        onDelete={vi.fn()}
        onDraw={vi.fn()}
        onCancelDraw={vi.fn()}
      />,
    );

    expect(
      screen.queryByText('Сначала создайте другой рабочий участок'),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Северный участок',
      }),
    );
    expect(
      screen.getByRole('menuitem', { name: 'Удалить участок' }),
    ).toHaveAttribute('aria-disabled', 'true');
    expect(
      screen.getByText('Сначала создайте другой рабочий участок'),
    ).toBeInTheDocument();
  });

  it('keeps keyboard navigation and Escape inside the row menu without closing its dialog', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog open title="Рабочие участки" onClose={onClose}>
        <PlantingZoneManager
          zones={zones}
          zoneUsage={{ a: 2 }}
          onFocus={vi.fn()}
          onRename={vi.fn()}
          onRedraw={vi.fn()}
          onDelete={vi.fn()}
          onDraw={vi.fn()}
          onCancelDraw={vi.fn()}
        />
      </Dialog>,
    );
    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(dialog).toHaveFocus());
    const trigger = screen.getByRole('button', {
      name: 'Действия с участком 1: Северный участок',
    });
    await tabTo(user, trigger);
    await user.keyboard('{ArrowDown}');
    const menu = screen.getByRole('menu');
    const rename = within(menu).getByRole('menuitem', {
      name: 'Переименовать',
    });
    expect(rename).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    const redraw = within(menu).getByRole('menuitem', {
      name: 'Изменить контур',
    });
    expect(redraw).toHaveFocus();
    await user.keyboard('{End}');
    const deletion = within(menu).getByRole('menuitem', {
      name: 'Удалить участок',
    });
    expect(deletion).toHaveFocus();
    expect(deletion).toHaveAccessibleDescription(
      'Сначала перенесите или удалите 2 посадки',
    );
    await user.keyboard('{Home}');
    expect(rename).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(onClose).not.toHaveBeenCalled();
    await user.keyboard('{ArrowUp}');
    expect(
      screen.getByRole('menuitem', { name: 'Изменить контур' }),
    ).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(
      screen.getByRole('menuitem', { name: 'Удалить участок' }),
    ).toHaveFocus();
    await user.tab();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('keeps selected IDs outside a search result when toggling visible rows or choosing all', () => {
    const onSelectionChange = vi.fn();
    render(
      <PlantingZoneManager
        zones={zones}
        activeIds={['b']}
        onSelectionChange={onSelectionChange}
        onFocus={vi.fn()}
        onRename={vi.fn()}
        onRedraw={vi.fn()}
        onDelete={vi.fn()}
        onDraw={vi.fn()}
        onCancelDraw={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Поиск участков' }), {
      target: { value: 'Северный' },
    });
    expect(screen.getByText('Найдено: 1')).toBeInTheDocument();
    expect(
      screen.queryByRole('checkbox', { name: 'Южный участок' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Северный участок' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith(['b', 'a']);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать все' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith(['a', 'b']);
    fireEvent.click(screen.getByRole('button', { name: 'Снять выбор' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  });

  it('continues Tab to the next row and wraps the last row back inside the dialog', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <>
        <button type="button">За диалогом</button>
        <Dialog open title="Рабочие участки" onClose={onClose}>
          <PlantingZoneManager
            zones={zones}
            activeIds={['a']}
            onSelectionChange={vi.fn()}
            onFocus={vi.fn()}
            onRename={vi.fn()}
            onRedraw={vi.fn()}
            onDelete={vi.fn()}
            onDraw={vi.fn()}
            onCancelDraw={vi.fn()}
          />
        </Dialog>
      </>,
    );
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    const first = screen.getByRole('button', {
      name: 'Действия с участком 1: Северный участок',
    });
    await tabTo(user, first);
    await user.keyboard('{ArrowDown}');
    await user.tab();
    expect(
      screen.getByRole('checkbox', { name: 'Южный участок' }),
    ).toHaveFocus();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    const last = screen.getByRole('button', {
      name: 'Действия с участком 2: Южный участок',
    });
    await tabTo(user, last);
    await user.keyboard('{ArrowDown}');
    await user.tab();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Закрыть' })).toHaveFocus(),
    );
    expect(
      screen.getByRole('button', { name: 'За диалогом', hidden: true }),
    ).not.toHaveFocus();
    expect(onClose).not.toHaveBeenCalled();
    await tabTo(user, last);
    await user.keyboard('{ArrowDown}');
    await user.tab({ shift: true });
    expect(last).toHaveFocus();
    await user.tab({ shift: true });
    expect(
      screen.getByRole('button', { name: 'Показать участок 2: Южный участок' }),
    ).toHaveFocus();
  });

  it('continues an inline menu through surrounding document controls without a modal focus trap', async () => {
    const user = userEvent.setup();
    render(
      <>
        <PlantingZoneManager
          zones={zones}
          onFocus={vi.fn()}
          onRename={vi.fn()}
          onRedraw={vi.fn()}
          onDelete={vi.fn()}
          onDraw={vi.fn()}
          onCancelDraw={vi.fn()}
        />
        <button type="button">Следующий раздел</button>
      </>,
    );
    const last = screen.getByRole('button', {
      name: 'Действия с участком 2: Южный участок',
    });
    await tabTo(user, last);
    await user.keyboard('{ArrowDown}');
    await user.tab();
    expect(
      screen.getByRole('button', { name: 'Следующий раздел' }),
    ).toHaveFocus();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('filters selected and empty areas without changing the working selection', () => {
    const onSelectionChange = vi.fn();
    render(
      <PlantingZoneManager
        zones={zones}
        activeIds={['a']}
        zoneUsage={{ a: 3 }}
        onSelectionChange={onSelectionChange}
        onFocus={vi.fn()}
        onRename={vi.fn()}
        onRedraw={vi.fn()}
        onDelete={vi.fn()}
        onDraw={vi.fn()}
        onCancelDraw={vi.fn()}
      />,
    );
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Фильтр участков' }),
      { target: { value: 'selected' } },
    );
    expect(
      screen.getByRole('checkbox', { name: 'Северный участок' }),
    ).toBeChecked();
    expect(screen.queryByText('Южный участок')).not.toBeInTheDocument();
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Фильтр участков' }),
      { target: { value: 'empty' } },
    );
    expect(
      screen.getByRole('checkbox', { name: 'Южный участок' }),
    ).not.toBeChecked();
    expect(screen.queryByText('Северный участок')).not.toBeInTheDocument();
    expect(onSelectionChange).not.toHaveBeenCalled();
  });

  it('keeps drawing and saving gates on the compact controls', () => {
    const onDraw = vi.fn();
    const onCancelDraw = vi.fn();
    const onRename = vi.fn();
    const props = {
      zones,
      activeIds: ['a'],
      onSelectionChange: vi.fn(),
      onFocus: vi.fn(),
      onRename,
      onRedraw: vi.fn(),
      onDelete: vi.fn(),
      onDraw,
      onCancelDraw,
    };
    const view = render(<PlantingZoneManager {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Новый участок' }));
    expect(onDraw).toHaveBeenCalledOnce();
    view.rerender(<PlantingZoneManager {...props} drawing />);
    fireEvent.click(screen.getByRole('button', { name: 'Отменить обводку' }));
    expect(onCancelDraw).toHaveBeenCalledOnce();
    view.rerender(<PlantingZoneManager {...props} saving />);
    expect(
      screen.getByRole('button', { name: 'Новый участок' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Выбрать все' })).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Действия с участком 1: Северный участок',
      }),
    );
    expect(
      screen.getByText('Дождитесь завершения сохранения'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Переименовать' }));
    expect(onRename).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('textbox', {
        name: 'Название участка 1: Северный участок',
      }),
    ).not.toBeInTheDocument();
  });
});
