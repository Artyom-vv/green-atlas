import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DadataSetup } from './DadataSetup';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('saves a user key through the desktop session without rendering it again', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce({ ok: true })
    .mockResolvedValueOnce({ ok: true });
  vi.stubGlobal('fetch', fetch);
  const saved = vi.fn();
  render(<DadataSetup onSaved={saved} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Настроить DaData' }),
  );
  fireEvent.change(screen.getByLabelText('API-ключ DaData'), {
    target: { value: 'private-key-123' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Сохранить ключ локально' }),
  );
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(fetch).toHaveBeenLastCalledWith(
    '/_desktop/location/dadata',
    expect.objectContaining({
      method: 'POST',
      credentials: 'same-origin',
      body: JSON.stringify({ api_key: 'private-key-123' }),
    }),
  );
  expect(screen.queryByDisplayValue('private-key-123')).not.toBeInTheDocument();
});

it('does not offer local settings on the hosted web app', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: false });
  vi.stubGlobal('fetch', fetch);
  render(<DadataSetup onSaved={vi.fn()} />);
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(
    screen.queryByRole('button', { name: 'Настроить DaData' }),
  ).not.toBeInTheDocument();
});
