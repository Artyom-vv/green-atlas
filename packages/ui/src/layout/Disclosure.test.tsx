import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Info } from 'lucide-react';
import { ControlProvider, Disclosure, IconButton, TextInput } from '../index';

it('opens a panel from its description and preserves the draft when collapsed', () => {
  render(
    <Disclosure variant="panel" title="Слои" description="124 слоя чертежа">
      <TextInput aria-label="Выбор роли" defaultValue="Здание" />
    </Disclosure>,
  );
  const trigger = screen.getByRole('button', { name: 'Слои 124 слоя чертежа' });
  fireEvent.click(screen.getByText('124 слоя чертежа'));
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('textbox', { name: 'Выбор роли' })).toBeVisible();
  fireEvent.click(trigger);
  expect(
    screen.queryByRole('textbox', { name: 'Выбор роли' }),
  ).not.toBeInTheDocument();
  fireEvent.click(trigger);
  expect(screen.getByRole('textbox', { name: 'Выбор роли' })).toHaveValue(
    'Здание',
  );
});

it('keeps collapsed fields mounted but hidden/inert and inherits compact density', () => {
  render(
    <ControlProvider size="compact">
      <Disclosure title="Подробности" variant="plain">
        <TextInput aria-label="Черновик" defaultValue="Сохранить" />
      </Disclosure>
    </ControlProvider>,
  );
  const button = screen.getByRole('button', { name: 'Подробности' });
  const content = document.getElementById(
    button.getAttribute('aria-controls')!,
  );
  expect(button).toHaveAttribute('data-size', 'compact');
  expect(content).toHaveAttribute('hidden');
  expect(content).toHaveAttribute('inert');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  fireEvent.click(button);
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'Черновик' },
  });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(screen.getByRole('textbox')).toHaveValue('Черновик');
});

it('keeps the auxiliary action focusable and independent from expansion', () => {
  const action = vi.fn();
  const changed = vi.fn();
  render(
    <Disclosure
      variant="section"
      title="Граница расчёта"
      onOpenChange={changed}
      actions={
        <IconButton icon={<Info />} label="О выборе границы" onClick={action} />
      }
    >
      <TextInput aria-label="Граница" />
    </Disclosure>,
  );
  const trigger = screen.getByRole('button', { name: 'Граница расчёта' });
  const help = screen.getByRole('button', { name: 'О выборе границы' });
  expect(help.parentElement?.closest('button')).toBeNull();
  help.focus();
  expect(help).toHaveFocus();
  fireEvent.click(help);
  expect(action).toHaveBeenCalledOnce();
  expect(changed).not.toHaveBeenCalled();
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  trigger.focus();
  expect(trigger).toHaveFocus();
  fireEvent.click(trigger);
  expect(changed).toHaveBeenCalledWith(true);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(action).toHaveBeenCalledOnce();
});

it('keeps trailing group actions independent and retains collapsed fields', () => {
  const visibility = vi.fn();
  render(
    <ControlProvider size="compact">
      <Disclosure
        title="Инженерные сети"
        variant="plain"
        headerVariant="trailing-actions"
        actions={
          <IconButton
            icon={Info}
            label="Скрыть инженерные сети"
            onClick={visibility}
          />
        }
      >
        <TextInput aria-label="Слой" defaultValue="Канализация" />
      </Disclosure>
    </ControlProvider>,
  );
  const trigger = screen.getByRole('button', { name: 'Инженерные сети' });
  const action = screen.getByRole('button', { name: 'Скрыть инженерные сети' });
  expect(action.parentElement?.closest('button')).toBeNull();
  fireEvent.click(action);
  expect(visibility).toHaveBeenCalledOnce();
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(trigger);
  expect(screen.getByRole('textbox', { name: 'Слой' })).toHaveValue(
    'Канализация',
  );
  fireEvent.click(trigger);
  fireEvent.click(trigger);
  expect(screen.getByRole('textbox', { name: 'Слой' })).toHaveValue(
    'Канализация',
  );
});
