import { type PropsWithChildren } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, renderHook, waitFor, act } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  SceneSnapshot,
  SceneRenderPreparation,
  RenderCamera,
} from '@green/api-client';
import {
  scenePreparationActions,
  useScenePreparation,
} from './useScenePreparation';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const snapshot = {
  project_id: 'kit',
  content_sha256: 'a'.repeat(64),
  horizon_year: 0,
  geometry_version: 3,
  plan_version: 7,
} as SceneSnapshot;
const camera: RenderCamera = {
  position: [20, 1.7, 30],
  quaternion: [0, 0, 0, 1],
  fov_y_deg: 42,
  aspect: 1.5,
  near: 0.04,
  far: 2000,
};
function prepared(source = snapshot): SceneRenderPreparation {
  return {
    id: 'prepared-kit',
    sha256: 'b'.repeat(64),
    project_id: source.project_id!,
    scene_sha256: source.content_sha256!,
    horizon_year: source.horizon_year,
    geometry_version: source.geometry_version!,
    plan_version: source.plan_version,
    camera,
    preview: {
      asset_version: 0,
      meshes: [],
      curbs: [],
      context_trees: [],
      suppressed_source_ids: [],
    },
    summary: {
      message: 'Готово',
      context_tree_count: 0,
      map_building_count: 0,
      map_status: 'unaligned',
      conditional_building_count: 0,
      osm_building_count: 0,
      terrain_status: 'missing',
    },
    provenance: {},
  };
}
function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

it('keeps composition pinned when only the camera moves, and refreshes explicitly', async () => {
  const prepare = vi
    .spyOn(scenePreparationActions, 'prepare')
    .mockResolvedValue(prepared());
  let pose = camera;
  const view = renderHook(
    () =>
      useScenePreparation({ snapshot, enabled: true, getCamera: () => pose }),
    { wrapper: wrapper() },
  );
  await waitFor(() =>
    expect(view.result.current.preparation?.id).toBe('prepared-kit'),
  );
  pose = { ...camera, position: [40, 1.7, 30] };
  view.rerender();
  expect(prepare).toHaveBeenCalledOnce();
  act(() => view.result.current.refresh());
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(2));
  expect(prepare.mock.calls[1][1].camera).toEqual(pose);
});

it('starts a clock only with the request, retains it on cached remount and resets it for explicit refresh', async () => {
  let now = 10_000;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
  let finish!: (value: SceneRenderPreparation) => void;
  const prepare = vi
    .spyOn(scenePreparationActions, 'prepare')
    .mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const sharedWrapper = wrapper();
  const view = renderHook(
    ({ enabled }) =>
      useScenePreparation({ snapshot, enabled, getCamera: () => camera }),
    { wrapper: sharedWrapper, initialProps: { enabled: false } },
  );
  expect(view.result.current.startedAt).toBeUndefined();
  expect(prepare).not.toHaveBeenCalled();
  view.rerender({ enabled: true });
  await waitFor(() => expect(view.result.current.startedAt).toBe(10_000));
  now = 14_000;
  view.rerender({ enabled: false });
  expect(view.result.current.startedAt).toBe(10_000);
  await act(async () => finish(prepared()));
  view.unmount();
  now = 20_000;
  const reopened = renderHook(
    () =>
      useScenePreparation({ snapshot, enabled: true, getCamera: () => camera }),
    { wrapper: sharedWrapper },
  );
  expect(reopened.result.current.startedAt).toBe(10_000);
  expect(prepare).toHaveBeenCalledOnce();
  act(() => reopened.result.current.refresh());
  await waitFor(() => expect(reopened.result.current.startedAt).toBe(20_000));
  expect(prepare).toHaveBeenCalledTimes(2);
  await act(async () => finish(prepared()));
});

it('does not carry a request clock into an unstarted scene revision', async () => {
  vi.spyOn(scenePreparationActions, 'prepare').mockResolvedValue(prepared());
  const view = renderHook(
    ({ source, enabled }) =>
      useScenePreparation({
        snapshot: source,
        enabled,
        getCamera: () => camera,
      }),
    { wrapper: wrapper(), initialProps: { source: snapshot, enabled: true } },
  );
  await waitFor(() => expect(view.result.current.preparation).toBeDefined());
  expect(view.result.current.startedAt).toBeTypeOf('number');
  view.rerender({ source: { ...snapshot, horizon_year: 10 }, enabled: false });
  expect(view.result.current.preparation).toBeUndefined();
  expect(view.result.current.startedAt).toBeUndefined();
});

it('does not display a delayed preparation from an earlier source revision', async () => {
  let resolveFirst!: (value: SceneRenderPreparation) => void;
  const next = { ...snapshot, content_sha256: 'c'.repeat(64), plan_version: 8 };
  const prepare = vi
    .spyOn(scenePreparationActions, 'prepare')
    .mockImplementationOnce(
      () =>
        new Promise<SceneRenderPreparation>((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockResolvedValueOnce(prepared(next));
  const view = renderHook(
    ({ source }) =>
      useScenePreparation({
        snapshot: source,
        enabled: true,
        getCamera: () => camera,
      }),
    { initialProps: { source: snapshot }, wrapper: wrapper() },
  );
  await waitFor(() => expect(prepare).toHaveBeenCalledOnce());
  view.rerender({ source: next });
  await waitFor(() =>
    expect(view.result.current.preparation?.plan_version).toBe(8),
  );
  await act(async () => {
    resolveFirst(prepared());
  });
  expect(view.result.current.preparation?.scene_sha256).toBe(
    next.content_sha256,
  );
  expect(view.result.current.preparation?.plan_version).toBe(8);
});

it('retains the displayed preparation while refreshing and after a failed refresh', async () => {
  let rejectRefresh!: (error: Error) => void;
  let finishRetry!: (value: SceneRenderPreparation) => void;
  vi.spyOn(scenePreparationActions, 'prepare')
    .mockResolvedValueOnce(prepared())
    .mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          rejectRefresh = reject;
        }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishRetry = resolve;
        }),
    );
  const view = renderHook(
    () =>
      useScenePreparation({ snapshot, enabled: true, getCamera: () => camera }),
    { wrapper: wrapper() },
  );
  await waitFor(() =>
    expect(view.result.current.preparation?.id).toBe('prepared-kit'),
  );
  act(() => view.result.current.refresh());
  await waitFor(() => expect(view.result.current.loading).toBe(true));
  expect(view.result.current.preparation?.id).toBe('prepared-kit');
  await act(async () => rejectRefresh(new Error('temporary failure')));
  await waitFor(() => expect(view.result.current.loading).toBe(false));
  expect(view.result.current.preparation?.id).toBe('prepared-kit');
  expect(view.result.current.error?.message).toBe('temporary failure');
  act(() => view.result.current.refresh());
  await waitFor(() => expect(view.result.current.loading).toBe(true));
  expect(view.result.current.error).toBeNull();
  expect(view.result.current.preparation?.id).toBe('prepared-kit');
  await act(async () => finishRetry({ ...prepared(), id: 'retry-result' }));
  await waitFor(() => expect(view.result.current.loading).toBe(false));
  expect(view.result.current.preparation?.id).toBe('retry-result');
  expect(view.result.current.error).toBeNull();
});

it('never retains a previous preparation across a scene revision change', async () => {
  vi.spyOn(scenePreparationActions, 'prepare')
    .mockResolvedValueOnce(prepared())
    .mockImplementationOnce(() => new Promise(() => {}));
  const view = renderHook(
    ({ source }) =>
      useScenePreparation({
        snapshot: source,
        enabled: true,
        getCamera: () => camera,
      }),
    { initialProps: { source: snapshot }, wrapper: wrapper() },
  );
  await waitFor(() => expect(view.result.current.preparation).toBeDefined());
  view.rerender({ source: { ...snapshot, plan_version: 8 } });
  expect(view.result.current.preparation).toBeUndefined();
});

it('queues the newly selected street camera while an earlier preparation is still running', async () => {
  let finish!: (value: SceneRenderPreparation) => void;
  const prepare = vi
    .spyOn(scenePreparationActions, 'prepare')
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(prepared());
  let pose = camera;
  const view = renderHook(
    () =>
      useScenePreparation({ snapshot, enabled: true, getCamera: () => pose }),
    { wrapper: wrapper() },
  );
  await waitFor(() => expect(prepare).toHaveBeenCalledOnce());
  pose = { ...camera, position: [99, 1.7, 55] };
  act(() => view.result.current.refresh());
  await act(async () => finish(prepared()));
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(2));
  expect(prepare.mock.calls[1][1].camera).toEqual(pose);
});

it('keeps inactive 2D from starting preparation and rejects a mismatched response', async () => {
  const prepare = vi
    .spyOn(scenePreparationActions, 'prepare')
    .mockResolvedValue(prepared({ ...snapshot, project_id: 'other' }));
  const view = renderHook(
    ({ enabled }) =>
      useScenePreparation({ snapshot, enabled, getCamera: () => camera }),
    { initialProps: { enabled: false }, wrapper: wrapper() },
  );
  expect(prepare).not.toHaveBeenCalled();
  view.rerender({ enabled: true });
  await waitFor(() =>
    expect(view.result.current.error?.message).toContain('другой версии'),
  );
  expect(view.result.current.preparation).toBeUndefined();
});
