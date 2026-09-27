import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { Button, Dialog, FormActions } from '../index';
afterEach(cleanup);
it('owns one header and a footer outside the scrolling body', () => {
  render(
    <Dialog
      open
      title="Допустимая область"
      onClose={vi.fn()}
      footer={
        <FormActions layout="equal">
          <Button>Сделать рабочим участком</Button>
        </FormActions>
      }
    >
      <p>Объект исходного DXF</p>
    </Dialog>,
  );
  const dialog = screen.getByRole('dialog');
  const body = dialog.querySelector('[data-slot="dialog-body"]');
  const header = dialog.querySelector('[data-slot="dialog-header"]');
  const footer = dialog.querySelector('[data-slot="dialog-footer"]');
  expect(header?.parentElement).toBe(dialog);
  expect(footer?.parentElement).toBe(dialog);
  expect(body).not.toContainElement(
    screen.getByRole('button', { name: 'Сделать рабочим участком' }),
  );
  expect(footer).toContainElement(
    screen.getByRole('button', { name: 'Сделать рабочим участком' }),
  );
  expect(
    screen.getAllByRole('heading', { name: 'Допустимая область' }),
  ).toHaveLength(1);
});
it('does not render an empty footer when its conditional action is absent', () => {
  render(
    <Dialog open title="Информация" onClose={vi.fn()} footer={false}>
      Описание
    </Dialog>,
  );
  expect(
    screen.getByRole('dialog').querySelector('[data-slot="dialog-footer"]'),
  ).toBeNull();
});
it('opts registry windows into a stable frame without changing confirmation dialogs', () => {
  const { rerender } = render(
    <Dialog open stableHeight size="wide" title="Реестр" onClose={vi.fn()}>
      Строки
    </Dialog>,
  );
  expect(screen.getByRole('dialog')).toHaveAttribute(
    'data-stable-height',
    'true',
  );
  rerender(
    <Dialog open title="Подтверждение" onClose={vi.fn()}>
      Текст
    </Dialog>,
  );
  expect(screen.getByRole('dialog')).not.toHaveAttribute(
    'data-stable-height',
    'true',
  );
});
it('keeps form state when temporarily returning to a map', () => {
  function Form() {
    const [value, setValue] = useState('');
    return (
      <input
        aria-label="Параметр"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
    );
  }
  const { rerender } = render(
    <Dialog open keepMounted title="Настройки" onClose={vi.fn()}>
      <Form />
    </Dialog>,
  );
  fireEvent.change(screen.getByLabelText('Параметр'), {
    target: { value: '12' },
  });
  rerender(
    <Dialog open={false} keepMounted title="Настройки" onClose={vi.fn()}>
      <Form />
    </Dialog>,
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  rerender(
    <Dialog open keepMounted title="Настройки" onClose={vi.fn()}>
      <Form />
    </Dialog>,
  );
  expect(screen.getByLabelText('Параметр')).toHaveValue('12');
});
it('Escape closes only the topmost dialog', () => {
  const outer = vi.fn(),
    inner = vi.fn();
  const { rerender } = render(
    <Dialog open title="Настройки" onClose={outer}>
      {null}
    </Dialog>,
  );
  rerender(
    <>
      <Dialog open title="Настройки" onClose={outer}>
        {null}
      </Dialog>
      <Dialog open title="Каталог" onClose={inner}>
        {null}
      </Dialog>
    </>,
  );
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(inner).toHaveBeenCalledOnce();
  expect(outer).not.toHaveBeenCalled();
});
