import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ActionRow } from '../index';

it('opens its task with an accessible label and respects the disabled state', () => {
  const open = vi.fn();
  const { rerender } = render(
    <ActionRow onClick={open}>Собранные области</ActionRow>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Собранные области' }));
  expect(open).toHaveBeenCalledOnce();
  rerender(
    <ActionRow disabled onClick={open}>
      Собранные области
    </ActionRow>,
  );
  const disabled = screen.getByRole('button', { name: 'Собранные области' });
  expect(disabled).toBeDisabled();
  fireEvent.click(disabled);
  expect(open).toHaveBeenCalledOnce();
});
