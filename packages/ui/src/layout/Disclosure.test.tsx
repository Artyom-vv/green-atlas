import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ControlProvider, Disclosure, TextInput } from '../index';

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
