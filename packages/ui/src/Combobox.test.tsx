import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Combobox } from './index';

describe('Combobox', () => {
  it('filters and chooses an option with the keyboard', () => {
    const onChange = vi.fn();
    render(<Combobox options={[{ value: 'lime', label: 'Липа', description: 'Tilia cordata' }, { value: 'oak', label: 'Дуб', description: 'Quercus robur' }]} onChange={onChange} />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'дуб' } });
    expect(screen.getByRole('option', { name: /Дуб/ })).toBeVisible();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('oak');
  });
});
