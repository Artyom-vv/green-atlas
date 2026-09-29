import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SourceSection, SourceSections } from './SourceSection';

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

it('opens only one main panel and preserves edits and nested review state', () => {
  render(
    <SourceSections defaultSection="02">
      <SourceSection number="01" title="Территория">
        <input aria-label="Граница" defaultValue="Участок" />
      </SourceSection>
      <SourceSection number="02" title="Слои">
        <input aria-label="Поиск слоя" />
      </SourceSection>
    </SourceSections>,
  );
  const territory = screen.getByRole('button', { name: 'Территория' });
  const layers = screen.getByRole('button', { name: 'Слои' });
  expect(layers).toHaveAttribute('aria-expanded', 'true');
  expect(territory).toHaveAttribute('aria-expanded', 'false');
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск слоя' }), {
    target: { value: 'водопровод' },
  });
  fireEvent.click(territory);
  expect(layers).toHaveAttribute('aria-expanded', 'false');
  expect(
    screen.queryByRole('textbox', { name: 'Поиск слоя' }),
  ).not.toBeInTheDocument();
  fireEvent.click(layers);
  expect(territory).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByRole('textbox', { name: 'Поиск слоя' })).toHaveValue(
    'водопровод',
  );
  fireEvent.click(layers);
  expect(layers).toHaveAttribute('aria-expanded', 'false');
});

it('moves header focus with arrows and Home/End without intercepting form inputs', () => {
  render(
    <SourceSections defaultSection="01">
      <SourceSection number="01" title="Территория">
        <input aria-label="Граница" />
      </SourceSection>
      <SourceSection number="02" title="Слои">
        Слои
      </SourceSection>
      <SourceSection number="03" title="Геометрия">
        Контуры
      </SourceSection>
    </SourceSections>,
  );
  const territory = screen.getByRole('button', { name: 'Территория' });
  const layers = screen.getByRole('button', { name: 'Слои' });
  const geometry = screen.getByRole('button', { name: 'Геометрия' });
  territory.focus();
  fireEvent.keyDown(territory, { key: 'ArrowDown' });
  expect(layers).toHaveFocus();
  fireEvent.keyDown(layers, { key: 'End' });
  expect(geometry).toHaveFocus();
  fireEvent.keyDown(geometry, { key: 'Home' });
  expect(territory).toHaveFocus();
  fireEvent.keyDown(territory, { key: 'ArrowUp' });
  expect(geometry).toHaveFocus();
  const input = screen.getByRole('textbox');
  input.focus();
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  expect(input).toHaveFocus();
  expect(territory).toHaveAttribute('aria-expanded', 'true');
});
