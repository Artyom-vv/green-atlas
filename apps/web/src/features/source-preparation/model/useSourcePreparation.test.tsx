import type { FC, ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClientError, type Project } from '@green/api-client';
import { preparationApi } from '../api/preparationApi';
import { useSourcePreparation } from './useSourcePreparation';

const source: Project = {
  id: 'project',
  name: 'Участок',
  status: 'mapped',
  state_version: 1,
  geometry_version: 1,
  map_ready: false,
  layers: [
    {
      id: 'road',
      source_name: 'ROAD',
      suggested_kind: 'road',
      mapped_kind: 'road',
      color: '#000',
      object_count: 1,
      geometry_complete: true,
      linetype: 'CONTINUOUS',
      required: false,
      visible: true,
    },
  ],
};
afterEach(() => vi.restoreAllMocks());

describe('source preparation form recovery', () => {
  it('requires an explicit territory choice when several usable contours exist', async () => {
    vi.spyOn(preparationApi, 'getProject').mockResolvedValue({
      ...source,
      layers: [
        ...(source.layers ?? []),
        {
          id: 'order-boundary',
          source_name: 'Граница заказа',
          suggested_kind: 'ignore',
          mapped_kind: 'ignore',
          color: '#000',
          object_count: 1,
          geometry_complete: true,
          linetype: 'CONTINUOUS',
          required: false,
          visible: true,
          boundary_candidate: {
            status: 'usable',
            basis: 'polygonized_linework',
            area_m2: 10_000,
            inset_1_5m_area_m2: 9_409,
            component_count: 1,
          },
        },
      ],
    });
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper: Wrapper },
    );

    await waitFor(() =>
      expect(result.current.readinessBlockedReason).toBe(
        'Выберите один контур территории для расчёта.',
      ),
    );
    act(() =>
      result.current.setMappings({
        road: { layer_id: 'road', kind: 'road', visible: true },
        'order-boundary': {
          layer_id: 'order-boundary',
          kind: 'site_border',
          visible: true,
        },
      }),
    );
    expect(result.current.readinessBlockedReason).toBeUndefined();
    unmount();
    client.clear();
  });

  it('keeps CAD preview navigation independent of calculation status', async () => {
    vi.spyOn(preparationApi, 'getProject').mockResolvedValue({
      ...source,
      import_status: {
        mode: 'cad_preview',
        editability: 'read_only',
        message: 'Предварительная карта',
      },
    });
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    const latest = vi
      .spyOn(preparationApi, 'getLatestOperation')
      .mockRejectedValue(new Error('offline'));
    const save = vi.spyOn(preparationApi, 'saveMappings');
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.cadPreview).toBe(true));
    expect(latest).not.toHaveBeenCalled();
    expect(result.current.preparationBlocked).toBe(false);
    expect(result.current.checkingStatus).toBe(false);
    expect(result.current.statusUnknown).toBe(false);
    act(() => result.current.saveMutation.mutate());
    expect(save).not.toHaveBeenCalled();
    unmount();
    client.clear();
  });

  it('preserves edits and the original conflict when refetch returns cached data plus an error', async () => {
    const conflict = new ApiClientError(
      'PROJECT_VERSION_CONFLICT',
      'Проект изменился',
    );
    vi.spyOn(preparationApi, 'getProject').mockResolvedValue(source);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'saveMappings').mockRejectedValue(conflict);
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const Wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.preparationBlocked).toBe(false));
    act(() =>
      result.current.setMappings({
        road: { layer_id: 'road', kind: 'utility', visible: true },
      }),
    );
    act(() => result.current.saveMutation.mutate());
    await waitFor(() => expect(result.current.mutationError).toBe(conflict));
    vi.mocked(preparationApi.getProject).mockRejectedValueOnce(
      new TypeError('offline'),
    );
    await act(async () => {
      await result.current.reloadAfterConflict();
    });
    expect(result.current.mappings.road.kind).toBe('utility');
    expect(result.current.mutationError).toBe(conflict);
    expect(result.current.preparationRecovery?.message).toContain(
      'Ваши изменения',
    );
    expect(result.current.preparationBlocked).toBe(true);
    await act(async () => {
      await result.current.reloadAfterConflict();
    });
    expect(result.current.mappings.road.kind).toBe('road');
    expect(result.current.mutationError).toBeFalsy();
    expect(result.current.preparationRecovery).toBeUndefined();
    unmount();
    client.clear();
  });
});
