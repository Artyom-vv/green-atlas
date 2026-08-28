import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Button, Dialog, EmptyState, FormField, IconButton, InlineMessage, NumberStepper, Progress, Select, StepProgress, TextInput } from './index';
import { Plus } from 'lucide-react';

describe('Button', () => {
  it('supports interaction and loading state', () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Сохранить</Button>);
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(onClick).toHaveBeenCalledOnce();
    rerender(<Button loading>Сохранить</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('keeps the default 36px contract and exposes explicit compact and large sizes', () => {
    render(<>
      <Button>Обычная</Button>
      <Button controlSize="compact">Компактная</Button>
      <IconButton icon={Plus} label="Крупная иконка" controlSize="large" />
      <TextInput aria-label="Компактное поле" controlSize="compact" />
      <Select aria-label="Крупный выбор" controlSize="large"><option>Значение</option></Select>
    </>);
    expect(screen.getByRole('button', { name: 'Обычная' })).toHaveAttribute('data-size', 'default');
    expect(screen.getByRole('button', { name: 'Компактная' })).toHaveClass('ui-control--compact');
    expect(screen.getByRole('button', { name: 'Крупная иконка' })).toHaveClass('ui-control--large');
    expect(screen.getByRole('textbox', { name: 'Компактное поле' })).toHaveClass('ui-control--compact');
    expect(screen.getByRole('combobox', { name: 'Крупный выбор' })).toHaveClass('ui-control--large');
  });

  it('exposes disabled, empty and error states accessibly', () => {
    render(<><Button disabled>Недоступно</Button><EmptyState title="Нет данных" description="Измените фильтры" /><InlineMessage tone="error">Сбой загрузки</InlineMessage><FormField label="Название" error="Обязательное поле"><TextInput /></FormField><Progress label="Расчёт" value={45} /></>);
    expect(screen.getByRole('button', { name: 'Недоступно' })).toBeDisabled();
    expect(screen.getByText('Сбой загрузки').closest('[role="alert"]')).toHaveTextContent('Сбой загрузки');
    expect(screen.getByText('Нет данных')).toBeInTheDocument();
    expect(screen.getByText('Обязательное поле')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Расчёт' })).toHaveAttribute('aria-valuenow', '45');
  });

  it('keeps step progress and numeric changes accessible', () => {
    const onChange = vi.fn();
    render(<><StepProgress current={1} steps={[{ id: 'goal', label: 'Цель' }, { id: 'zones', label: 'Участки' }]} /><NumberStepper label="Деревья" value={12} onChange={onChange} /></>);
    expect(screen.getByText('Шаг 2 из 2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Увеличить: Деревья' }));
    expect(onChange).toHaveBeenCalledWith(13);
  });

  it('traps dialog focus, closes on Escape and restores the opener', async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <><Button onClick={() => setOpen(true)}>Открыть</Button><Dialog open={open} title="Подтверждение" onClose={() => setOpen(false)} footer={<Button>Подтвердить</Button>}>Текст</Dialog></>;
    }
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Открыть' });
    opener.focus();
    fireEvent.click(opener);
    const close = await screen.findByRole('button', { name: 'Закрыть' });
    await waitFor(() => expect(close).toHaveFocus());
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: 'Подтвердить' })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });
});
