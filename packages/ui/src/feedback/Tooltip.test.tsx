import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Tooltip } from '../index';

it('attaches keyboard help to the native action without an extra control', async () => {
  const select = vi.fn();
  render(
    <Tooltip
      content="Полное название исходного слоя"
      render={<button onClick={select}>Слой</button>}
    />,
  );
  const action = screen.getByRole('button', { name: 'Слой' });
  expect(screen.getAllByRole('button')).toHaveLength(1);
  fireEvent.focus(action);
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'Полное название исходного слоя',
  );
  fireEvent.click(action);
  expect(select).toHaveBeenCalledOnce();
});
