import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { LocationServiceSettings } from '@green/api-client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LocationServiceSettingsForm } from './LocationServiceSettings';
import { locationServiceSettingsActions } from '../model/locationServiceSettingsActions';

const bundled: LocationServiceSettings = {
  configured: true,
  source: 'bundled',
  personal_key_configured: false,
  bundled_key_configured: true,
};
const personal: LocationServiceSettings = {
  ...bundled,
  source: 'personal',
  personal_key_configured: true,
};
beforeEach(() => {
  vi.spyOn(locationServiceSettingsActions, 'read').mockResolvedValue(bundled);
  vi.spyOn(locationServiceSettingsActions, 'save').mockResolvedValue(personal);
  vi.spyOn(locationServiceSettingsActions, 'reset').mockResolvedValue(bundled);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
async function open(onChanged = vi.fn()) {
  const view = render(<LocationServiceSettingsForm onChanged={onChanged} />);
  expect(locationServiceSettingsActions.read).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Настройки поиска адресов' }),
  );
  const input = await screen.findByLabelText('Личный API-ключ DaData');
  return { ...view, input, onChanged };
}

it('opens settings for a working app key and submits a private replacement with the keyboard', async () => {
  const user = userEvent.setup();
  const { input, onChanged } = await open();
  expect(
    screen.getByText('Сейчас используется: Ключ приложения'),
  ).toBeVisible();
  expect(input).toHaveAttribute('type', 'password');
  expect(input).toHaveAttribute('autocomplete', 'off');
  expect(input).toHaveValue('');
  expect(
    screen.queryByRole('button', { name: 'Использовать ключ приложения' }),
  ).not.toBeInTheDocument();
  await user.type(input, 'test-personal-key{Enter}');
  await screen.findByText('Ключ сохранён');
  expect(locationServiceSettingsActions.save).toHaveBeenCalledWith(
    { api_key: 'test-personal-key' },
    expect.any(AbortSignal),
  );
  expect(onChanged).toHaveBeenCalledWith(personal);
  expect(screen.getByText('Сейчас используется: Личный ключ')).toBeVisible();
  expect(input).toHaveValue('');
  expect(screen.queryByText('test-personal-key')).not.toBeInTheDocument();
  expect(screen.queryByText(/Ключ проверен/)).not.toBeInTheDocument();
});

it('clears an unsaved key on keyboard cancellation and does not persist it', async () => {
  const user = userEvent.setup();
  const { input, onChanged } = await open();
  await user.type(input, 'unsaved-key{Escape}');
  expect(
    screen.queryByRole('form', { name: 'Ключ DaData' }),
  ).not.toBeInTheDocument();
  expect(locationServiceSettingsActions.save).not.toHaveBeenCalled();
  expect(onChanged).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Настройки поиска адресов' }),
  );
  expect(await screen.findByLabelText('Личный API-ключ DaData')).toHaveValue(
    '',
  );
});

it('keeps the current source on a save failure, permits retry and never echoes a provider error containing the key', async () => {
  vi.mocked(locationServiceSettingsActions.save).mockRejectedValueOnce(
    new Error('Rejected private-example-key'),
  );
  const { input, onChanged } = await open();
  fireEvent.change(input, { target: { value: 'private-example-key' } });
  fireEvent.submit(screen.getByRole('form', { name: 'Ключ DaData' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Не удалось сохранить ключ. Повторите попытку',
  );
  expect(
    screen.queryByText(/Rejected|private-example-key/),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText('Сейчас используется: Ключ приложения'),
  ).toBeVisible();
  expect(onChanged).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить ключ' }));
  await screen.findByText('Ключ сохранён');
  expect(input).toHaveValue('');
  expect(onChanged).toHaveBeenCalledOnce();
});

it('removes a personal key explicitly and refreshes the search configuration without requesting its secret', async () => {
  vi.mocked(locationServiceSettingsActions.read).mockResolvedValue(personal);
  const { input, onChanged } = await open();
  expect(input).toHaveValue('');
  fireEvent.change(input, { target: { value: 'unused-draft-key' } });
  fireEvent.click(
    screen.getByRole('button', { name: 'Использовать ключ приложения' }),
  );
  await screen.findByText('Используется: Ключ приложения');
  expect(locationServiceSettingsActions.reset).toHaveBeenCalledWith(
    expect.any(AbortSignal),
  );
  expect(locationServiceSettingsActions.save).not.toHaveBeenCalled();
  expect(onChanged).toHaveBeenCalledWith(bundled);
  expect(input).toHaveValue('');
  expect(
    screen.queryByRole('button', { name: 'Использовать ключ приложения' }),
  ).not.toBeInTheDocument();
});

it('does not announce a failed reset as complete', async () => {
  vi.mocked(locationServiceSettingsActions.read).mockResolvedValue(personal);
  vi.mocked(locationServiceSettingsActions.reset).mockRejectedValueOnce(
    new Error('Write failed'),
  );
  const { onChanged } = await open();
  fireEvent.click(
    screen.getByRole('button', { name: 'Использовать ключ приложения' }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Не удалось переключить ключ',
  );
  expect(screen.getByText('Сейчас используется: Личный ключ')).toBeVisible();
  expect(onChanged).not.toHaveBeenCalled();
});

it('offers a retry when metadata cannot load', async () => {
  vi.mocked(locationServiceSettingsActions.read).mockRejectedValueOnce(
    new Error('Settings unavailable'),
  );
  render(<LocationServiceSettingsForm onChanged={vi.fn()} />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Настройки поиска адресов' }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Не удалось загрузить настройки',
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Повторить загрузку настроек' }),
  );
  expect(await screen.findByLabelText('Личный API-ключ DaData')).toHaveValue(
    '',
  );
});

it('cancels pending work and ignores a late save after the editor is unmounted', async () => {
  let finish!: (value: LocationServiceSettings) => void;
  vi.mocked(locationServiceSettingsActions.save).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { input, onChanged, unmount } = await open();
  fireEvent.change(input, { target: { value: 'unmounted-key' } });
  fireEvent.submit(screen.getByRole('form', { name: 'Ключ DaData' }));
  await waitFor(() =>
    expect(locationServiceSettingsActions.save).toHaveBeenCalledOnce(),
  );
  const signal = vi.mocked(locationServiceSettingsActions.save).mock
    .calls[0][1]!;
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish(personal));
  expect(onChanged).not.toHaveBeenCalled();
});
