import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { SceneSnapshot } from '@green/api-client';
import {
  SceneActivityProvider,
  useSceneActivity,
} from '../model/SceneActivityContext';
import { SceneReview } from './SceneReview';

const mock = vi.hoisted(() => {
  const controller = {
    updateEnvironment: vi.fn().mockResolvedValue(true),
    whenRendered: vi.fn(),
    cancelStreetPlacement: vi.fn(),
  };
  return { controller, controllerRef: { current: controller } };
});
vi.mock('../adapters/three/useSceneRenderer', () => ({
  useSceneRenderer: () => ({
    canvasRef: { current: null },
    controllerRef: mock.controllerRef,
    modelsReady: true,
    showRoots: false,
    setShowRoots: vi.fn(),
    retry: vi.fn(),
  }),
}));
function Slots() {
  const slots = useSceneActivity();
  return (
    <>
      <div data-testid="header" ref={slots?.setHeaderSlot} />
      <div data-testid="progress" ref={slots?.setProgressSlot} />
    </>
  );
}
afterEach(cleanup);
it('keeps header loading until the visible GPU frame without covering the canvas', async () => {
  let drawn!: (value: boolean) => void;
  mock.controller.whenRendered.mockReturnValue(
    new Promise<boolean>((resolve) => {
      drawn = resolve;
    }),
  );
  const snapshot = {
    project_id: 'frame-test',
    horizon_year: 0,
    objects: [],
  } as unknown as SceneSnapshot;
  const props = {
    snapshot,
    horizon: 0,
    selectedIds: [],
    onSelect: vi.fn(),
    onHorizon: vi.fn(),
    showGrowthControl: false,
  };
  const client = new QueryClient();
  const content = (loading = false, active = true) => (
    <QueryClientProvider client={client}>
      <SceneActivityProvider>
        <Slots />
        <SceneReview {...props} loading={loading} active={active} />
      </SceneActivityProvider>
    </QueryClientProvider>
  );
  const view = render(content(false, false));
  await waitFor(() =>
    expect(mock.controller.updateEnvironment).toHaveBeenCalled(),
  );
  expect(mock.controller.whenRendered).not.toHaveBeenCalled();
  expect(document.querySelector('[data-slot=viewport-loading]')).toBeNull();
  view.rerender(content());
  await waitFor(() => expect(mock.controller.whenRendered).toHaveBeenCalled());
  expect(document.querySelector('[data-slot=viewport-loading]')).toBeNull();
  expect(screen.getByTestId('header')).toHaveTextContent('Первый кадр');
  expect(
    screen.getByTestId('progress').querySelector('[data-slot=scene-pending]'),
  ).not.toBeNull();
  await act(async () => drawn(true));
  expect(screen.getByTestId('header')).not.toHaveTextContent('Первый кадр');
  expect(document.querySelector('[data-slot=viewport-loading]')).toBeNull();
  expect(screen.getByTestId('progress')).toBeEmptyDOMElement();
  view.rerender(content(true));
  expect(screen.getByTestId('header')).toHaveTextContent('Обновляем 3D');
  expect(document.querySelector('[data-slot=viewport-loading]')).toBeNull();
  let updated!: (value: boolean) => void;
  mock.controller.whenRendered.mockReturnValue(
    new Promise<boolean>((resolve) => {
      updated = resolve;
    }),
  );
  view.rerender(content(false));
  await waitFor(() =>
    expect(screen.getByTestId('header')).toHaveTextContent('Обновление кадра'),
  );
  expect(document.querySelector('[data-slot=viewport-loading]')).toBeNull();
  await act(async () => updated(true));
  expect(screen.getByTestId('progress')).toBeEmptyDOMElement();
});
