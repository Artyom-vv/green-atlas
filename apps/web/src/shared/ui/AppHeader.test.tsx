import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it } from 'vitest';
import { AppHeader } from './AppHeader';

it('keeps one project-list exit and the current project title', () => {
  render(
    <MemoryRouter>
      <AppHeader projectName="ВДНХ" />
    </MemoryRouter>,
  );
  expect(screen.getByRole('link', { name: 'Проекты' })).toHaveAttribute(
    'href',
    '/projects',
  );
  expect(screen.getByText('ВДНХ')).toBeVisible();
  expect(screen.getAllByRole('link')).toHaveLength(1);
});
