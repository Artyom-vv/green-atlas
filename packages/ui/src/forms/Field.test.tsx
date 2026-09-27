import { fireEvent, render, screen } from '@testing-library/react';
import { createRef, useState, type FC } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  Combobox,
  Field,
  NumberInput,
  Select,
  TextArea,
  TextInput,
} from '../index';

const NestedControl: FC = () => {
  const [value, setValue] = useState('');
  return (
    <div>
      <TextInput
        name="draft"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
    </div>
  );
};

describe('Field semantics', () => {
  it('keeps labels separate from hint text and descendant actions', () => {
    render(
      <>
        <Field label="Комментарий" hint="Черновик сохраняется">
          <TextInput />
        </Field>
        <Field label="Порода" hint="Начните вводить">
          <Combobox
            options={[{ value: 'lime', label: 'Липа' }]}
            onChange={vi.fn()}
          />
        </Field>
      </>,
    );
    expect(
      screen.getByRole('textbox', { name: 'Комментарий' }),
    ).toHaveAccessibleDescription('Черновик сохраняется');
    expect(
      screen.getByRole('combobox', { name: 'Порода' }),
    ).toHaveAccessibleDescription('Начните вводить');
    expect(
      screen
        .getByRole('button', { name: 'Показать варианты' })
        .closest('label'),
    ).toBeNull();
  });

  it('associates explicit IDs, composes descriptions and preserves custom accessible labels/ref', () => {
    const inputRef = createRef<HTMLInputElement>();
    const { rerender } = render(
      <>
        <span id="external-help">Другая подсказка</span>
        <Field label="Название" hint="Подсказка">
          <TextInput
            ref={inputRef}
            id="custom-id"
            aria-label="Особое имя"
            aria-describedby="external-help"
          />
        </Field>
      </>,
    );
    const input = screen.getByRole('textbox', { name: 'Особое имя' });
    expect(inputRef.current).toBe(input);
    expect(screen.getByText('Название')).toHaveAttribute('for', 'custom-id');
    expect(input).toHaveAccessibleDescription('Другая подсказка Подсказка');
    rerender(
      <Field label="Название" error="Нужно значение" required>
        <TextInput id="custom-id" />
      </Field>,
    );
    const invalid = screen.getByRole('textbox', { name: 'Название' });
    expect(invalid).toHaveAttribute('aria-invalid', 'true');
    expect(invalid).toBeRequired();
    expect(invalid).toHaveAccessibleDescription('Нужно значение');
  });

  it('preserves nested controlled value ownership and native events', () => {
    render(
      <Field label="Черновик">
        <NestedControl />
      </Field>,
    );
    const input = screen.getByRole('textbox', { name: 'Черновик' });
    fireEvent.change(input, { target: { value: 'новое значение' } });
    expect(input).toHaveValue('новое значение');
    expect(input).toHaveAttribute('name', 'draft');
  });

  it('applies metadata to number, select, textarea and disabled fields', () => {
    render(
      <>
        <Field label="Число" error="Проверьте диапазон">
          <NumberInput value={4} onValueChange={vi.fn()} />
        </Field>
        <Field label="Вариант" disabled>
          <Select>
            <option>Первый</option>
          </Select>
        </Field>
        <Field label="Описание" required>
          <TextArea />
        </Field>
      </>,
    );
    expect(
      screen.getByRole('spinbutton', { name: 'Число' }),
    ).toHaveAccessibleDescription('Проверьте диапазон');
    expect(screen.getByRole('combobox', { name: 'Вариант' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'Описание' })).toBeRequired();
  });
});
