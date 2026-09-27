import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SourceSection } from './SourceSection';

afterEach(cleanup);
it('makes the whole section heading and summary one accessible disclosure', () => {
  render(
    <SourceSection
      number="01"
      title="Слои"
      description="Выбор типов"
      defaultOpen={false}
    >
      <input aria-label="Тип" defaultValue="Здания" />
    </SourceSection>,
  );
  const button = screen.getByRole('button', { name: 'Слои Выбор типов' });
  expect(screen.getByRole('heading', { name: 'Слои' })).toBeVisible();
  expect(button).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(screen.getByText('Выбор типов'));
  expect(screen.getByRole('textbox', { name: 'Тип' })).toBeVisible();
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'Тротуар' },
  });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(screen.getByRole('textbox')).toHaveValue('Тротуар');
});
