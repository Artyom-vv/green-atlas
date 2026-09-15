import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MoreHorizontal, Trash } from 'lucide-react';
import { expect, it, vi } from 'vitest';
import {
  ControlProvider,
  IconButton,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from '../index';

it('supports icon composition and keyboard focus on disabled items without activation', async () => {
  const onDelete = vi.fn();
  render(
    <ControlProvider size="compact">
      <Menu modal={false}>
        <MenuTrigger
          render={
            <IconButton label="Действия участка" icon={<MoreHorizontal />} />
          }
        />
        <MenuPopup>
          <MenuItem>Показать на карте</MenuItem>
          <MenuSeparator />
          <MenuItem
            disabled
            aria-describedby="delete-reason"
            startIcon={<Trash color="red" size={40} />}
            onClick={onDelete}
          >
            Удалить участок
          </MenuItem>
          <div id="delete-reason">На участке есть посадки</div>
        </MenuPopup>
      </Menu>
    </ControlProvider>,
  );
  const trigger = screen.getByRole('button', { name: 'Действия участка' });
  act(() => trigger.focus());
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  const first = await screen.findByRole('menuitem', {
    name: 'Показать на карте',
  });
  await waitFor(() => expect(first).toHaveFocus());
  fireEvent.keyDown(first, { key: 'ArrowDown' });
  const disabled = screen.getByRole('menuitem', { name: 'Удалить участок' });
  await waitFor(() => expect(disabled).toHaveFocus());
  expect(disabled).toHaveAttribute('aria-disabled', 'true');
  expect(disabled).toHaveAccessibleDescription('На участке есть посадки');
  expect(disabled).toHaveAttribute('data-size', 'compact');
  fireEvent.keyDown(disabled, { key: 'Enter' });
  fireEvent.click(disabled);
  expect(onDelete).not.toHaveBeenCalled();
  expect(screen.getByRole('menu')).toBeVisible();
  fireEvent.keyDown(disabled, { key: 'Escape' });
  await waitFor(() =>
    expect(screen.queryByRole('menu')).not.toBeInTheDocument(),
  );
  await waitFor(() => expect(trigger).toHaveFocus());
});
