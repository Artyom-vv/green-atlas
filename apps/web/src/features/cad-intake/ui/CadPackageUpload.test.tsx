import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '@green/api-client';
import { CadPackageUpload } from './CadPackageUpload';

afterEach(() => vi.restoreAllMocks());

it('uploads all DXFs and lets the operator choose the main drawing', async () => {
  const rootId = 'upload-0123456789abcdef0123456789abcdef';
  vi.spyOn(api, 'uploadCadPackage').mockResolvedValue({
    root_id: rootId,
    total_bytes: 6,
    snapshots: [
      {
        root_id: rootId,
        path: 'genplan.dxf.green-atlas.snapshot.json',
        sha256: 'c'.repeat(64),
        bytes: 1,
      },
      {
        root_id: rootId,
        path: 'geobase.dxf.green-atlas.snapshot.json',
        sha256: 'd'.repeat(64),
        bytes: 1,
      },
    ],
    entries: [
      {
        root_id: rootId,
        path: 'genplan.dxf',
        sha256: 'a'.repeat(64),
        bytes: 3,
      },
      {
        root_id: rootId,
        path: 'geobase.dxf',
        sha256: 'b'.repeat(64),
        bytes: 3,
      },
    ],
  });
  const onStart = vi.fn();
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <CadPackageUpload busy={false} onStart={onStart} />
    </QueryClientProvider>,
  );
  const files = [
    new File(['one'], 'genplan.dxf'),
    new File(['{}'], 'genplan.dxf.green-atlas.snapshot.json'),
    new File(['two'], 'geobase.dxf'),
    new File(['{}'], 'geobase.dxf.green-atlas.snapshot.json'),
  ];
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {
    target: { files },
  });

  await screen.findByRole('radio', { name: 'genplan.dxf' });
  fireEvent.click(screen.getByRole('radio', { name: 'geobase.dxf' }));
  fireEvent.click(screen.getByRole('button', { name: 'Проверить комплект' }));

  await waitFor(() =>
    expect(onStart).toHaveBeenCalledWith({
      rootId,
      path: 'geobase.dxf',
      sha256: 'b'.repeat(64),
      additionalEntries: [{ path: 'genplan.dxf', sha256: 'a'.repeat(64) }],
    }),
  );
  expect(api.uploadCadPackage).toHaveBeenCalledWith(files);
});
