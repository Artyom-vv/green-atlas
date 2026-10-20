import { fireEvent, render, screen } from '@testing-library/react';
import { Search, X } from 'lucide-react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Field } from '../forms/Field';
import { ControlProvider } from '../foundations/ControlProvider';
import { TextInput } from './TextInput';

describe('TextInput', () => {
  it('preserves native refs, controlled values and events for ghost search', () => {
    const ref = createRef<HTMLInputElement>();
    const onChange = vi.fn();
    const { rerender } = render(
      <TextInput
        ref={ref}
        type="search"
        aria-label="Найти слой"
        variant="ghost"
        value="Дорога"
        onChange={onChange}
        startIcon={<Search size={40} color="red" />}
        endIcon={<X />}
      />,
    );
    const input = screen.getByRole('searchbox', { name: 'Найти слой' });
    expect(ref.current).toBe(input);
    expect(input).toHaveValue('Дорога');
    fireEvent.change(input, { target: { value: 'Сеть' } });
    expect(onChange).toHaveBeenCalledOnce();
    expect(input).toHaveValue('Дорога');
    expect(input).toHaveAttribute('data-variant', 'ghost');
    const icons = input.parentElement?.querySelectorAll('svg');
    expect(icons).toHaveLength(2);
    icons?.forEach((icon) => {
      expect(icon).toHaveAttribute('width', '16');
      expect(icon).toHaveAttribute('stroke', 'currentColor');
      expect(icon).toHaveAttribute('aria-hidden', 'true');
    });
    rerender(
      <TextInput
        ref={ref}
        type="search"
        aria-label="Найти слой"
        variant="ghost"
        value="Сеть"
        onChange={onChange}
        startIcon={<Search />}
      />,
    );
    expect(input).toHaveValue('Сеть');
    expect(ref.current).toBe(input);
  });

  it('retains field labels, errors, disabled state and density across variants', () => {
    const { rerender } = render(
      <ControlProvider size="compact">
        <Field label="Поиск" error="Уточните запрос" required disabled>
          <TextInput
            variant="ghost"
            aria-describedby="extra-hint"
            startIcon={<Search />}
          />
        </Field>
        <span id="extra-hint">Название слоя</span>
      </ControlProvider>,
    );
    const ghost = screen.getByRole('textbox', { name: 'Поиск' });
    expect(ghost).toBeDisabled();
    expect(ghost).toBeRequired();
    expect(ghost).toHaveAttribute('aria-invalid', 'true');
    expect(ghost).toHaveAccessibleDescription('Название слоя Уточните запрос');
    expect(ghost).toHaveAttribute('data-size', 'compact');
    rerender(
      <TextInput
        aria-label="Имя"
        controlSize="large"
        readOnly
        defaultValue="Липа"
      />,
    );
    const standard = screen.getByRole('textbox', { name: 'Имя' });
    expect(standard).toHaveAttribute('data-variant', 'default');
    expect(standard).toHaveAttribute('data-size', 'large');
    expect(standard).toHaveAttribute('readonly');
    expect(standard).toHaveValue('Липа');
  });
});
