import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Dialog } from './index';
afterEach(cleanup);
it('opts registry windows into a stable frame without changing confirmation dialogs', () => {
  const { rerender } = render(<Dialog open stableHeight size="wide" title="Реестр" onClose={vi.fn()}>Строки</Dialog>);
  expect(screen.getByRole('dialog')).toHaveClass('ui-dialog--stable');
  rerender(<Dialog open title="Подтверждение" onClose={vi.fn()}>Текст</Dialog>);
  expect(screen.getByRole('dialog')).not.toHaveClass('ui-dialog--stable');
});
it('keeps form state when temporarily returning to a map', () => {
  function Form() { const [value, setValue] = useState(''); return <input aria-label="Параметр" value={value} onChange={event => setValue(event.target.value)} />; }
  const { rerender } = render(<Dialog open keepMounted title="Настройки" onClose={vi.fn()}><Form /></Dialog>);
  fireEvent.change(screen.getByLabelText('Параметр'), { target: { value: '12' } });
  rerender(<Dialog open={false} keepMounted title="Настройки" onClose={vi.fn()}><Form /></Dialog>);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  rerender(<Dialog open keepMounted title="Настройки" onClose={vi.fn()}><Form /></Dialog>);
  expect(screen.getByLabelText('Параметр')).toHaveValue('12');
});
it('Escape closes only the topmost dialog', () => {
  const outer = vi.fn(), inner = vi.fn();
  const { rerender } = render(<Dialog open title="Настройки" onClose={outer}>{null}</Dialog>);
  rerender(<><Dialog open title="Настройки" onClose={outer}>{null}</Dialog><Dialog open title="Каталог" onClose={inner}>{null}</Dialog></>);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(inner).toHaveBeenCalledOnce(); expect(outer).not.toHaveBeenCalled();
});
