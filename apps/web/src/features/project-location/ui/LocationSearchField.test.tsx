import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { LocationSuggestions } from '@green/api-client';
import { locationSuggestion } from '../model/locationFixtures';
import { locationActions } from '../model/useProjectLocation';
import { LocationSearchField } from './LocationSearchField';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function Search({
  onSelect = vi.fn(),
  configurationRevision = 0,
}: {
  onSelect?: () => void;
  configurationRevision?: number;
}) {
  const [value, setValue] = useState('');
  return (
    <LocationSearchField
      projectId="project"
      value={value}
      onChange={setValue}
      onSelect={onSelect}
      configurationRevision={configurationRevision}
    />
  );
}
it('ignores a late result from the previous query', async () => {
  let finish!: (result: LocationSuggestions) => void;
  const suggest = vi
    .spyOn(locationActions, 'suggest')
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce({
      configured: true,
      suggestions: [
        {
          ...locationSuggestion,
          id: 'new',
          label: 'Новая улица',
          city: null,
          street: 'Новая улица',
        },
      ],
    });
  render(<Search />);
  const input = screen.getByRole('combobox');
  fireEvent.change(input, { target: { value: 'Старая' } });
  await waitFor(() => expect(suggest).toHaveBeenCalledTimes(1));
  fireEvent.change(input, { target: { value: 'Новая' } });
  await screen.findByRole('option', { name: 'Новая улица' });
  await act(async () =>
    finish({ configured: true, suggestions: [locationSuggestion] }),
  );
  expect(
    screen.queryByRole('option', { name: locationSuggestion.label }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Новая улица' })).toBeVisible();
});
it('cancels an old-key request when settings change, preserves the query and ignores its late result', async () => {
  let finish!: (result: LocationSuggestions) => void;
  const suggest = vi
    .spyOn(locationActions, 'suggest')
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce({
      configured: true,
      suggestions: [
        {
          ...locationSuggestion,
          id: 'new-key',
          label: 'Новая улица',
          city: null,
          street: 'Новая улица',
        },
      ],
    });
  const view = render(<Search />);
  const input = screen.getByRole('combobox');
  fireEvent.change(input, { target: { value: 'Парковая' } });
  await waitFor(() => expect(suggest).toHaveBeenCalledOnce());
  const oldSignal = suggest.mock.calls[0][2]!;
  view.rerender(<Search configurationRevision={1} />);
  expect(oldSignal.aborted).toBe(true);
  expect(input).toHaveValue('Парковая');
  await screen.findByRole('option', { name: 'Новая улица' });
  await act(async () =>
    finish({ configured: true, suggestions: [locationSuggestion] }),
  );
  expect(
    screen.queryByRole('option', { name: locationSuggestion.label }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Новая улица' })).toBeVisible();
});
it('supports keyboard selection and surfaces provider failures with retry', async () => {
  const onSelect = vi.fn();
  vi.spyOn(locationActions, 'suggest')
    .mockRejectedValueOnce(new Error('Нет соединения'))
    .mockResolvedValueOnce({
      configured: true,
      suggestions: [locationSuggestion],
    });
  render(<Search onSelect={onSelect} />);
  const input = screen.getByRole('combobox');
  fireEvent.change(input, { target: { value: 'Парковая' } });
  expect(await screen.findByRole('alert')).toHaveTextContent('Нет соединения');
  fireEvent.click(screen.getByRole('button', { name: 'Повторить поиск' }));
  await screen.findByRole('option');
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onSelect).toHaveBeenCalledWith(locationSuggestion);
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
});
