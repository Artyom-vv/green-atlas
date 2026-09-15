import { render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { expect, it, vi } from 'vitest';
import { App } from '../app/App';

vi.mock('@/features/assistant', () => ({
  ProjectAssistantProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('./WorkspacePage', () => ({
  WorkspacePage: () => <main>Рабочий редактор</main>,
}));
function Location() {
  return <output aria-label="Текущий адрес">{useLocation().pathname}</output>;
}

it('opens an existing IDE bookmark in the canonical workspace without a second editor implementation', async () => {
  render(
    <MemoryRouter initialEntries={['/projects/example/workspace/ide']}>
      <App />
      <Location />
    </MemoryRouter>,
  );
  expect(await screen.findByText('Рабочий редактор')).toBeInTheDocument();
  expect(screen.getByLabelText('Текущий адрес')).toHaveTextContent(
    '/projects/example/workspace',
  );
  expect(screen.getByLabelText('Текущий адрес')).not.toHaveTextContent(
    '/workspace/ide',
  );
});
