import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NumberInput } from '../index';

afterEach(cleanup);

function Harness({
  initial = 40,
  min = 2,
  max = 5000,
}: {
  initial?: number;
  min?: number;
  max?: number;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <NumberInput
        aria-label="Число"
        value={value}
        onValueChange={setValue}
        min={min}
        max={max}
        step={0.1}
      />
      <output aria-label="Применено">{value}</output>
    </>
  );
}

describe('NumberInput bounded numeric mode', () => {
  it('retains a cleared draft and publishes valid input without requiring blur', () => {
    render(<Harness />);
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '' } });
    expect(input).toHaveValue(null);
    expect(screen.getByLabelText('Применено')).toHaveTextContent('40');
    fireEvent.change(input, { target: { value: '120' } });
    expect(screen.getByLabelText('Применено')).toHaveTextContent('120');
  });

  it.each([
    ['9000', 5000],
    ['-3', 2],
  ])('clamps the out-of-range draft %s only on blur', (text, expected) => {
    render(<Harness />);
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: text } });
    expect(input).toHaveValue(Number(text));
    expect(screen.getByLabelText('Применено')).toHaveTextContent('40');
    fireEvent.blur(input);
    expect(input).toHaveValue(expected);
    expect(screen.getByLabelText('Применено')).toHaveTextContent(
      String(expected),
    );
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);
    expect(input).toHaveValue(expected);
  });

  it('keeps valid decimal text through a parent echo and normalizes it on blur', () => {
    render(<Harness initial={2} min={0} max={10} />);
    const input = screen.getByRole('spinbutton') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '1.20' } });
    expect(screen.getByLabelText('Применено')).toHaveTextContent('1.2');
    expect(input.value).toBe('1.20');
    fireEvent.blur(input);
    expect(input.value).toBe('1.2');
  });

  it('replaces a local partial draft when the owner supplies a different value', () => {
    const onValueChange = vi.fn();
    const { rerender } = render(
      <NumberInput value={4} onValueChange={onValueChange} min={0} max={10} />,
    );
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '' } });
    rerender(
      <NumberInput value={7} onValueChange={onValueChange} min={0} max={10} />,
    );
    expect(input).toHaveValue(7);
    expect(onValueChange).not.toHaveBeenCalled();
  });

  it('preserves native input use and numeric mode accessibility', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <NumberInput
        defaultValue={3}
        unit="м"
        aria-label="Длина"
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('spinbutton'), {
      target: { value: '5' },
    });
    expect(onChange).toHaveBeenCalledOnce();
    expect(screen.getByRole('spinbutton')).toHaveValue(5);
    rerender(
      <NumberInput
        value={5}
        onValueChange={vi.fn()}
        controlSize="compact"
        disabled
        aria-label="Длина"
        aria-invalid
        aria-describedby="length-error"
      />,
    );
    expect(screen.getByRole('spinbutton', { name: 'Длина' })).toBeDisabled();
    expect(screen.getByRole('spinbutton')).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    expect(screen.getByRole('spinbutton')).toHaveAttribute(
      'aria-describedby',
      'length-error',
    );
    expect(screen.getByRole('spinbutton')).toHaveAttribute(
      'data-size',
      'compact',
    );
  });
});
