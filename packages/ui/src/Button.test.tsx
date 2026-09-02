import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Button, Checkbox, Combobox, Dialog, Disclosure, EmptyState, FormField, HelpDisclosure, IconButton, InlineMessage, NumberStepper, Progress, Select, StepProgress, TextInput } from './index';
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
      <Checkbox label="Компактный флаг" controlSize="compact" />
    </>);
    expect(screen.getByRole('button', { name: 'Обычная' })).toHaveAttribute('data-size', 'default');
    expect(screen.getByRole('button', { name: 'Компактная' })).toHaveClass('ui-control--compact');
    expect(screen.getByRole('button', { name: 'Крупная иконка' })).toHaveClass('ui-control--large');
    expect(screen.getByRole('textbox', { name: 'Компактное поле' })).toHaveClass('ui-control--compact');
    expect(screen.getByRole('combobox', { name: 'Крупный выбор' })).toHaveClass('ui-control--large');
    expect(screen.getByRole('checkbox', { name: 'Компактный флаг' }).closest('label')).toHaveClass('ui-control--compact');
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
    const { container } = render(<><StepProgress current={1} steps={[{ id: 'goal', label: 'Цель' }, { id: 'zones', label: 'Участки' }]} /><NumberStepper label="Деревья" value={12} onChange={onChange} /></>);
    expect(screen.getByText('Участки')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAccessibleName('Этапы настройки: 2 из 2, Участки');
    expect(screen.queryByText(/Шаг/)).not.toBeInTheDocument();
    const progressSteps = container.querySelectorAll('.ui-step-progress__track > span');
    expect(progressSteps[0]).toHaveClass('is-complete');
    expect(progressSteps[0]).not.toHaveClass('is-current');
    expect(progressSteps[1]).toHaveClass('is-current');
    fireEvent.click(screen.getByRole('button', { name: 'Увеличить: Деревья' }));
    expect(onChange).toHaveBeenCalledWith(13);
  });

  it('disables every interaction in disabled compound controls', () => {
    const { container } = render(<><Combobox value="tree" options={[{ value: 'tree', label: 'Дуб' }]} disabled onChange={vi.fn()} /><NumberStepper label="Диаметр" value={12} disabled onChange={vi.fn()} /></>);

    expect(container.querySelector('.ui-combobox > input')).toBeDisabled();
    expect(screen.getByRole('spinbutton', { name: 'Диаметр' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Уменьшить: Диаметр' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Увеличить: Диаметр' })).toBeDisabled();
  });

  it('exposes disclosure and custom checkbox semantics', () => {
    render(<><Disclosure title="Дополнительно"><span>Настройка</span></Disclosure><HelpDisclosure title="Почему меньше"><span>Объяснение</span></HelpDisclosure><Checkbox label="Рабочий участок" /></>);
    const trigger = screen.getByRole('button', { name: 'Дополнительно' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const help = screen.getByRole('button', { name: 'Почему меньше' });
    fireEvent.click(help);
    expect(help).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('checkbox', { name: 'Рабочий участок' })).toBeInTheDocument();
  });

  it('accepts large numeric values from the keyboard', () => {
    const onChange = vi.fn();
    render(<NumberStepper label="Число посадок" value={50} onChange={onChange} min={1} max={5000} />);
    const input = screen.getByRole('spinbutton', { name: 'Число посадок' });
    fireEvent.change(input, { target: { value: '5000' } });
    expect(onChange).toHaveBeenCalledWith(5000);
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
