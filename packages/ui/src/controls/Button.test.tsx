import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Plus } from 'lucide-react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  Button,
  Checkbox,
  Combobox,
  Dialog,
  Disclosure,
  EmptyState,
  FormField,
  HelpDisclosure,
  IconButton,
  InlineMessage,
  NumberStepper,
  Progress,
  Select,
  StepProgress,
  TextInput,
} from '../index';

describe('Button', () => {
  it.each(['disabled', 'loading'] as const)(
    'blocks activation on an %s icon button and keeps its reason',
    (state) => {
      const onClick = vi.fn();
      render(
        <>
          <IconButton
            icon={<Plus />}
            label="Добавить"
            {...{ [state]: true }}
            aria-describedby="reason"
            onClick={onClick}
          />
          <span id="reason">Дождитесь завершения запроса</span>
        </>,
      );
      const button = screen.getByRole('button', { name: 'Добавить' });
      expect(button).toBeDisabled();
      expect(button).toHaveAccessibleDescription(
        'Дождитесь завершения запроса',
      );
      button.click();
      fireEvent.keyDown(button, { key: 'Enter' });
      fireEvent.keyUp(button, { key: 'Enter' });
      expect(onClick).not.toHaveBeenCalled();
      expect(button).toHaveAttribute('tabindex', '-1');
    },
  );
  it.each([true, 'true'] as const)(
    'suppresses an icon tooltip while its popup is expanded (%s)',
    (expanded) => {
      const { rerender, unmount } = render(
        <IconButton
          icon={Plus}
          label="Действия"
          aria-haspopup="menu"
          aria-expanded={false}
        />,
      );
      const trigger = screen.getByRole('button', { name: 'Действия' });
      fireEvent.mouseEnter(trigger);
      const tooltip = screen.getByRole('tooltip');
      expect(trigger).toHaveAttribute('aria-describedby', tooltip.id);
      rerender(
        <IconButton
          icon={Plus}
          label="Действия"
          aria-haspopup="menu"
          aria-expanded={expanded}
        />,
      );
      fireEvent.focus(trigger);
      fireEvent.mouseEnter(trigger);
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
      expect(trigger).not.toHaveAttribute('aria-describedby');
      expect(trigger).toHaveAccessibleName('Действия');
      rerender(
        <IconButton
          icon={Plus}
          label="Действия"
          aria-haspopup="menu"
          aria-expanded={false}
        />,
      );
      expect(screen.getByRole('tooltip')).toHaveTextContent('Действия');
      fireEvent.blur(trigger);
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
      unmount();
    },
  );

  it('supports interaction and loading state', () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Сохранить</Button>);
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(onClick).toHaveBeenCalledOnce();
    rerender(<Button loading>Сохранить</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('keeps the default 36px contract and exposes explicit compact and large sizes', () => {
    render(
      <>
        <Button>Обычная</Button>
        <Button controlSize="compact">Компактная</Button>
        <IconButton icon={Plus} label="Крупная иконка" controlSize="large" />
        <TextInput aria-label="Компактное поле" controlSize="compact" />
        <Select aria-label="Крупный выбор" controlSize="large">
          <option>Значение</option>
        </Select>
        <Checkbox label="Компактный флаг" controlSize="compact" />
      </>,
    );
    expect(screen.getByRole('button', { name: 'Обычная' })).toHaveAttribute(
      'data-size',
      'default',
    );
    expect(screen.getByRole('button', { name: 'Компактная' })).toHaveAttribute(
      'data-size',
      'compact',
    );
    expect(
      screen.getByRole('button', { name: 'Крупная иконка' }),
    ).toHaveAttribute('data-size', 'large');
    expect(
      screen.getByRole('textbox', { name: 'Компактное поле' }),
    ).toHaveAttribute('data-size', 'compact');
    expect(
      screen.getByRole('combobox', { name: 'Крупный выбор' }),
    ).toHaveAttribute('data-size', 'large');
    expect(
      screen
        .getByRole('checkbox', { name: 'Компактный флаг' })
        .closest('label'),
    ).toHaveAttribute('data-size', 'compact');
  });

  it('exposes disabled, empty and error states accessibly', () => {
    render(
      <>
        <Button disabled>Недоступно</Button>
        <EmptyState title="Нет данных" description="Измените фильтры" />
        <InlineMessage tone="error">Сбой загрузки</InlineMessage>
        <FormField label="Название" error="Обязательное поле">
          <TextInput />
        </FormField>
        <Progress label="Расчёт" value={45} />
      </>,
    );
    expect(screen.getByRole('button', { name: 'Недоступно' })).toBeDisabled();
    expect(
      screen.getByText('Сбой загрузки').closest('[role="alert"]'),
    ).toHaveTextContent('Сбой загрузки');
    expect(screen.getByText('Нет данных')).toBeInTheDocument();
    expect(screen.getByText('Обязательное поле')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Расчёт' })).toHaveAttribute(
      'aria-valuenow',
      '45',
    );
  });

  it('keeps step progress and numeric changes accessible', () => {
    const onChange = vi.fn();
    const { container } = render(
      <>
        <StepProgress
          current={1}
          steps={[
            { id: 'goal', label: 'Цель' },
            { id: 'zones', label: 'Участки' },
          ]}
        />
        <NumberStepper label="Деревья" value={12} onChange={onChange} />
      </>,
    );
    expect(screen.getByText('Участки')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAccessibleName(
      'Этапы настройки: 2 из 2, Участки',
    );
    expect(screen.queryByText(/Шаг/)).not.toBeInTheDocument();
    const progressSteps = container.querySelectorAll('[data-step-state]');
    expect(progressSteps[0]).toHaveAttribute('data-step-state', 'complete');
    expect(progressSteps[0]).not.toHaveAttribute('data-step-state', 'current');
    expect(progressSteps[1]).toHaveAttribute('data-step-state', 'current');
    fireEvent.click(screen.getByRole('button', { name: 'Увеличить: Деревья' }));
    expect(onChange).toHaveBeenCalledWith(13);
  });

  it('disables every interaction in disabled compound controls', () => {
    render(
      <>
        <Combobox
          value="tree"
          options={[{ value: 'tree', label: 'Дуб' }]}
          disabled
          onChange={vi.fn()}
        />
        <NumberStepper label="Диаметр" value={12} disabled onChange={vi.fn()} />
      </>,
    );

    expect(screen.getByRole('combobox')).toBeDisabled();
    expect(screen.getByRole('spinbutton', { name: 'Диаметр' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Уменьшить: Диаметр' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Увеличить: Диаметр' }),
    ).toBeDisabled();
  });

  it('exposes disclosure and custom checkbox semantics', () => {
    render(
      <>
        <Disclosure title="Дополнительно">
          <span>Настройка</span>
        </Disclosure>
        <HelpDisclosure title="Почему меньше">
          <span>Объяснение</span>
        </HelpDisclosure>
        <Checkbox label="Рабочий участок" />
      </>,
    );
    const trigger = screen.getByRole('button', { name: 'Дополнительно' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const help = screen.getByRole('button', { name: 'Почему меньше' });
    fireEvent.click(help);
    expect(help).toHaveAttribute('aria-expanded', 'true');
    expect(
      screen.getByRole('checkbox', { name: 'Рабочий участок' }),
    ).toBeInTheDocument();
  });

  it('accepts large numeric values from the keyboard', () => {
    const onChange = vi.fn();
    render(
      <NumberStepper
        label="Число посадок"
        value={50}
        onChange={onChange}
        min={1}
        max={5000}
      />,
    );
    const input = screen.getByRole('spinbutton', { name: 'Число посадок' });
    fireEvent.change(input, { target: { value: '5000' } });
    expect(onChange).toHaveBeenCalledWith(5000);
  });

  it('discards an out-of-range stepper draft on Escape without committing it', () => {
    const onChange = vi.fn();
    render(
      <NumberStepper
        label="Количество"
        value={12}
        min={1}
        max={20}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole('spinbutton', { name: 'Количество' });
    input.focus();
    fireEvent.change(input, { target: { value: '99' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(input).toHaveValue(12);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('traps dialog focus, closes on Escape and restores the opener', async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button onClick={() => setOpen(true)}>Открыть</Button>
          <Dialog
            open={open}
            title="Подтверждение"
            onClose={() => setOpen(false)}
            footer={<Button>Подтвердить</Button>}
          >
            Текст
          </Dialog>
        </>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Открыть' });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveFocus());
    // Browser acceptance covers native Tab traversal; jsdom has no layout/tab order.
    expect(dialog).toContainElement(
      screen.getByRole('button', { name: 'Подтвердить' }),
    );
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    expect(opener).toHaveFocus();
  });
});
