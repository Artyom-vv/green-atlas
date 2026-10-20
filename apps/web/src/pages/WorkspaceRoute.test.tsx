import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { App } from '../app/App';

vi.mock('./WorkspacePage', () => ({
  WorkspacePage: () => <main>Рабочий редактор</main>,
}));
function Location() {
  return <output aria-label="Текущий адрес">{useLocation().pathname}</output>;
}

it('opens an existing IDE bookmark in the canonical workspace without a second editor implementation', async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={['/projects/example/workspace/ide']}>
        <App />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText('Рабочий редактор')).toBeInTheDocument();
  expect(screen.getByLabelText('Текущий адрес')).toHaveTextContent(
    '/projects/example/workspace',
  );
  expect(screen.getByLabelText('Текущий адрес')).not.toHaveTextContent(
    '/workspace/ide',
  );
});
