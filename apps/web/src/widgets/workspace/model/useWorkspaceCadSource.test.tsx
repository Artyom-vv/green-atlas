import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { NativeDxfSourceAsset, Project } from '@green/api-client';
import {
  nativeAsset,
  nativeProject,
} from '@/entities/source-data/model/nativeDxfFixtures';
import { useWorkspaceCadSource } from './useWorkspaceCadSource';

const transport = vi.hoisted(() => ({ native: vi.fn(), latest: vi.fn() }));
vi.mock('@green/api-client', async (load) => {
  const original = await load<typeof import('@green/api-client')>();
  return {
    ...original,
    api: {
      ...original.api,
      getNativeDxfSourceAsset: transport.native,
      getLatestOperation: transport.latest,
    },
  };
});
const clients: QueryClient[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  transport.native.mockResolvedValue(nativeAsset);
});
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
});

function mount(project: Project = nativeProject) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(({ project }) => useWorkspaceCadSource(project), {
    initialProps: { project },
    wrapper,
  });
}

describe('workspace native DXF display and interaction ownership', () => {
  it('keeps vector requests enabled during loading, after ready and after GPU failure', async () => {
    const { result } = mount();
    expect(result.current.vectorGeometryEnabled).toBe(true);
    await waitFor(() =>
      expect(result.current.source?.sha256).toBe(nativeAsset.source_sha256),
    );
    expect(result.current.source?.visualOwnership).toBe('source');
    act(() => result.current.onRenderState({ status: 'ready' }));
    expect(result.current.baseReady).toBe(true);
    expect(result.current.vectorGeometryEnabled).toBe(true);
    act(() =>
      result.current.onRenderState({
        status: 'error',
        message: 'GPU unavailable',
      }),
    );
    expect(result.current.baseReady).toBe(false);
    expect(result.current.vectorGeometryEnabled).toBe(true);
    expect(transport.latest).not.toHaveBeenCalled();
  });

  it('does not refetch or reload source identity when plan/state/layer presentation changes', async () => {
    const { result, rerender } = mount();
    await waitFor(() => expect(result.current.source).toBeDefined());
    const original = result.current.source!;
    act(() => result.current.onRenderState({ status: 'ready' }));
    rerender({
      project: {
        ...nativeProject,
        state_version: 99,
        geometry_version: 4,
        layers: [
          {
            id: 'pipes',
            source_name: 'Pipes',
            mapped_kind: 'utility',
            suggested_kind: 'utility',
            object_count: 2,
            color: '#111111',
            linetype: 'CONTINUOUS',
            geometry_complete: true,
            required: false,
            visible: false,
          },
        ],
      },
    });
    expect(result.current.source?.url).toBe(original.url);
    expect(result.current.source?.sha256).toBe(original.sha256);
    expect(result.current.source?.layerRoles).toEqual({ Pipes: 'utility' });
    expect(result.current.baseReady).toBe(true);
    expect(transport.native).toHaveBeenCalledTimes(1);
  });

  it('cancels a previous metadata request and rejects stale data after source replacement', async () => {
    let finish: (asset: NativeDxfSourceAsset) => void = () => undefined;
    transport.native.mockImplementationOnce(
      () =>
        new Promise<NativeDxfSourceAsset>((resolve) => {
          finish = resolve;
        }),
    );
    const { result, rerender } = mount();
    await waitFor(() => expect(transport.native).toHaveBeenCalledTimes(1));
    const firstSignal = transport.native.mock.calls[0][1] as AbortSignal;
    const replacement = {
      ...nativeProject,
      source_file: {
        ...nativeProject.source_file!,
        content_sha256: 'b'.repeat(64),
      },
    };
    const secondAsset = {
      ...nativeAsset,
      source_sha256: 'b'.repeat(64),
      file_url: nativeAsset.file_url.replace('a'.repeat(64), 'b'.repeat(64)),
    };
    transport.native.mockResolvedValue(secondAsset);
    rerender({ project: replacement });
    await waitFor(() =>
      expect(result.current.source?.sha256).toBe(secondAsset.source_sha256),
    );
    expect(firstSignal.aborted).toBe(true);
    await act(async () => finish(nativeAsset));
    expect(result.current.source?.sha256).toBe(secondAsset.source_sha256);
  });

  it('uses the existing vector fallback for unavailable or unqualified native sources', async () => {
    transport.native.mockRejectedValue(
      new Error('NATIVE_DXF_ASSET_UNAVAILABLE'),
    );
    const { result, rerender } = mount();
    await waitFor(() => expect(result.current.error).toBeDefined());
    expect(result.current.source).toBeUndefined();
    expect(result.current.vectorGeometryEnabled).toBe(true);
    rerender({
      project: {
        ...nativeProject,
        import_status: {
          ...nativeProject.import_status!,
          mode: 'release_bundle',
        },
      },
    });
    expect(result.current.source).toBeUndefined();
    expect(result.current.error).toBeUndefined();
    expect(transport.native).toHaveBeenCalledTimes(1);
  });
});
