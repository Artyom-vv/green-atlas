import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { EditorNumber } from './EditorPanel';

afterEach(cleanup);
function Harness() {
  const [value, setValue] = useState(40);
  return <><EditorNumber label="Количество" value={value} onChange={setValue} min={2} max={5000} /><output aria-label="Значение запроса">{value}</output></>;
}
describe('EditorNumber', () => {
  it('allows clearing and typing without sending an invalid intermediate value', () => {
    render(<Harness />);
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '' } });
    expect(input).toHaveValue(null);
    expect(screen.getByLabelText('Значение запроса')).toHaveTextContent('40');
    fireEvent.change(input, { target: { value: '120' } });
    expect(screen.getByLabelText('Значение запроса')).toHaveTextContent('120');
  });
  it('bounds out-of-range input on blur and restores a cleared value', () => {
    render(<Harness />);
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '9000' } });
    expect(screen.getByLabelText('Значение запроса')).toHaveTextContent('40');
    fireEvent.blur(input);
    expect(input).toHaveValue(5000);
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);
    expect(input).toHaveValue(5000);
  });
});
