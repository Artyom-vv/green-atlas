import { fireEvent, render, screen } from '@testing-library/react';
import { Plus } from 'lucide-react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  Button,
  Checkbox,
  ControlProvider,
  Icon,
  IconButton,
  NumberInput,
  Select,
  TextInput,
} from '../index';

describe('shared control contracts', () => {
  it('replaces the label scaffold for a rich content slot while preserving button behavior', () => {
    const onClick = vi.fn();
    render(
      <Button
        aria-label="Карточка породы"
        className="grid grid-cols-[40px_1fr]"
        onClick={onClick}
        content={
          <>
            <svg data-testid="preview" width="40" height="40" />
            <span>Липа</span>
          </>
        }
      />,
    );
    const button = screen.getByRole('button', { name: 'Карточка породы' });
    expect(screen.getByTestId('preview').parentElement).toBe(button);
    expect(button.querySelector('[data-slot="button-label"]')).toBeNull();
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('normalizes icon nodes and legacy constructors without changing arbitrary content', () => {
    render(
      <>
        <Button startIcon={<Plus size={40} color="red" />}>Добавить</Button>
        <Icon icon={Plus} label="Слой" size={20} />
        <Button>
          <svg data-testid="custom-content" width="72" height="12" />
          <span>Диаграмма</span>
        </Button>
      </>,
    );
    const icon = screen
      .getByRole('button', { name: 'Добавить' })
      .querySelector('svg');
    expect(icon).toHaveAttribute('width', '16');
    expect(icon).toHaveAttribute('stroke', 'currentColor');
    expect(icon).toHaveAttribute('aria-hidden', 'true');
    expect(
      screen.getByRole('img', { name: 'Слой' }).querySelector('svg'),
    ).toHaveAttribute('width', '20');
    expect(screen.getByTestId('custom-content')).toHaveAttribute('width', '72');
  });

  it('inherits density through composition with explicit local overrides', () => {
    render(
      <ControlProvider size="compact">
        <Button>Действие</Button>
        <TextInput aria-label="Имя" />
        <Select aria-label="Выбор">
          <option>Один</option>
        </Select>
        <Checkbox label="Флаг" />
        <IconButton
          controlSize="large"
          icon={<Plus />}
          label="Переопределение"
        />
      </ControlProvider>,
    );
    expect(screen.getByRole('button', { name: 'Действие' })).toHaveAttribute(
      'data-size',
      'compact',
    );
    expect(screen.getByRole('textbox')).toHaveAttribute('data-size', 'compact');
    expect(screen.getByRole('combobox')).toHaveAttribute(
      'data-size',
      'compact',
    );
    expect(screen.getByRole('checkbox').closest('label')).toHaveAttribute(
      'data-size',
      'compact',
    );
    expect(
      screen.getByRole('button', { name: 'Переопределение' }),
    ).toHaveAttribute('data-size', 'large');
  });

  it('forwards React 19 native refs to the actual controls', () => {
    const button = createRef<HTMLButtonElement>();
    const input = createRef<HTMLInputElement>();
    const number = createRef<HTMLInputElement>();
    const checkbox = createRef<HTMLInputElement>();
    const select = createRef<HTMLSelectElement>();
    render(
      <>
        <Button ref={button}>Сохранить</Button>
        <TextInput ref={input} aria-label="Текст" />
        <NumberInput ref={number} aria-label="Число" />
        <Checkbox ref={checkbox} label="Флаг" />
        <Select ref={select}>
          <option>Один</option>
        </Select>
      </>,
    );
    expect(button.current).toBe(screen.getByRole('button'));
    expect(input.current).toBe(screen.getByRole('textbox'));
    expect(number.current).toBe(screen.getByRole('spinbutton'));
    expect(checkbox.current).toBe(screen.getByRole('checkbox'));
    expect(select.current).toBe(screen.getByRole('combobox'));
  });

  it('does not implicitly submit a form and permits explicit submit actions', () => {
    const onSubmit = vi.fn((event) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Button>Изменить</Button>
        <Button type="submit">Применить</Button>
      </form>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }));
    expect(onSubmit).toHaveBeenCalledOnce();
  });
});
