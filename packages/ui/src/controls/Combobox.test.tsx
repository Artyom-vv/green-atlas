import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Button, Combobox, Dialog, Field } from '../index';

describe('Combobox', () => {
  it('filters and chooses an option with the keyboard', async () => {
    const onChange = vi.fn();
    render(
      <Combobox
        options={[
          { value: 'lime', label: 'Липа', description: 'Tilia cordata' },
          { value: 'oak', label: 'Дуб', description: 'Quercus robur' },
        ]}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole('combobox');
    act(() => input.focus());
    fireEvent.change(input, { target: { value: 'дуб' } });
    expect(await screen.findByRole('option', { name: /Дуб/ })).toBeVisible();
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    await waitFor(() => expect(input).toHaveAttribute('aria-activedescendant'));
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('oak'));
  });

  it('keeps the validation input out of the accessibility tree and submits the ID', () => {
    const { container } = render(
      <form>
        <Field label="Порода">
          <Combobox
            name="species"
            value="lime"
            options={[{ value: 'lime', label: 'Липа' }]}
            onChange={vi.fn()}
          />
        </Field>
      </form>,
    );
    expect(screen.getByRole('combobox')).toHaveAccessibleName('Порода');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    const hidden = container.querySelector('input[aria-hidden="true"]');
    expect(hidden).toHaveAttribute('tabindex', '-1');
    expect(
      new FormData(container.querySelector('form')!).getAll('species'),
    ).toEqual(['lime']);
  });

  it('closes the list on first Escape, then the dialog without clearing selection', async () => {
    const selected = vi.fn();
    function Example() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button onClick={() => setOpen(true)}>Открыть каталог</Button>
          <Dialog open={open} title="Каталог" onClose={() => setOpen(false)}>
            <Combobox
              aria-label="Найти породу"
              value="oak"
              options={[
                { value: 'lime', label: 'Липа' },
                { value: 'oak', label: 'Дуб' },
              ]}
              onChange={selected}
            />
          </Dialog>
        </>
      );
    }
    render(<Example />);
    const trigger = screen.getByRole('button', { name: 'Открыть каталог' });
    act(() => trigger.focus());
    fireEvent.click(trigger);
    const search = await screen.findByRole('combobox');
    act(() => search.focus());
    fireEvent.change(search, { target: { value: 'Липа' } });
    expect(await screen.findByRole('option', { name: 'Липа' })).toBeVisible();
    fireEvent.keyDown(search, { key: 'Escape' });
    await waitFor(() =>
      expect(search).toHaveAttribute('aria-expanded', 'false'),
    );
    expect(screen.getByRole('dialog')).toBeVisible();
    fireEvent.keyDown(search, { key: 'Escape' });
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(selected).not.toHaveBeenCalled();
  });
});
