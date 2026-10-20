import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from '@tanstack/react-query';
import { setLocalRuntimeAvailable } from '@green/api-client';
import { afterEach, expect, it, vi } from 'vitest';
import { LocalRuntimeBoundary } from '../app/LocalRuntimeBoundary';

afterEach(() => {
  cleanup();
  setLocalRuntimeAvailable(true);
});
it('keeps the current draft and refreshes current reads after restoration without replaying a write', async () => {
  const read = vi.fn().mockResolvedValue({ version: 1 });
  const preview = vi.fn().mockResolvedValue({});
  function Document() {
    useQuery({ queryKey: ['workspace-project', 'p'], queryFn: read });
    useQuery({
      queryKey: ['zone-review', 'p'],
      queryFn: preview,
      staleTime: Infinity,
    });
    return <input aria-label="Draft" defaultValue="unsaved" />;
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Document />
      <LocalRuntimeBoundary />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(read).toHaveBeenCalledOnce());
  await waitFor(() => expect(preview).toHaveBeenCalledOnce());
  act(() => setLocalRuntimeAvailable(false));
  expect(
    await screen.findByRole('dialog', { name: 'Локальный сервис остановлен' }),
  ).toBeVisible();
  expect(screen.getByLabelText('Draft')).toHaveValue('unsaved');
  act(() => setLocalRuntimeAvailable(true));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText('Draft')).toHaveValue('unsaved');
  expect(preview).toHaveBeenCalledOnce();
});

it.each([
  'map-features',
  'workspace-cad-source',
  'project-release',
  'preparation-completion',
])('refreshes a cancelled %s read when the runtime returns', async (key) => {
  const read = vi
    .fn()
    .mockImplementationOnce(() => new Promise(() => undefined))
    .mockResolvedValue('recovered');
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Read() {
    const query = useQuery({
      queryKey: [key, 'p'],
      queryFn: ({ signal }) => {
        signal.throwIfAborted();
        return read();
      },
    });
    return <span>{query.data}</span>;
  }
  render(
    <QueryClientProvider client={client}>
      <Read />
      <LocalRuntimeBoundary />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(read).toHaveBeenCalledOnce());
  await act(async () => {
    setLocalRuntimeAvailable(false);
    await client.cancelQueries({ queryKey: [key, 'p'] });
  });
  act(() => setLocalRuntimeAvailable(true));
  expect(await screen.findByText('recovered')).toBeVisible();
  expect(read).toHaveBeenCalledTimes(2);
});
