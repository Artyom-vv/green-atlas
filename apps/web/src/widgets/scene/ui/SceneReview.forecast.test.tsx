import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { SceneSnapshot } from '@green/api-client';
import { SceneReview } from './SceneReview';

const scene = vi.hoisted(() => {
  const controller = {
    beginStreetPlacement: vi.fn(),
    cancelStreetPlacement: vi.fn(),
    updateEnvironment: vi.fn().mockResolvedValue(true),
    whenRendered: vi.fn().mockResolvedValue(true),
    exportRenderCamera: vi.fn(),
    levelStreetHorizon: vi.fn(),
  };
  return { controller, controllerRef: { current: controller } };
});
vi.mock('../adapters/three/useSceneRenderer', () => ({
  useSceneRenderer: () => ({
    canvasRef: { current: null },
    controllerRef: scene.controllerRef,
    modelsReady: true,
    renderError: undefined,
    showRoots: false,
    setShowRoots: vi.fn(),
    retry: vi.fn(),
  }),
}));

const snapshot = (year = 0): SceneSnapshot => ({
  project_id: 'street-forecast',
  content_sha256: `${year}`.repeat(64),
  geometry_version: 2,
  plan_version: 4,
  horizon_year: year,
  coordinate_origin: [1000, 2000],
  georeference_status: 'local',
  geometry_source: 'prepared_geometry',
  completeness: 'partial',
  terrain_status: 'missing',
  building_heights_status: 'missing',
  building_feature_count: 0,
  building_height_confirmed_count: 0,
  note: 'Прогноз роста',
  data_gaps: [],
  objects: [],
});
const props = {
  selectedIds: [],
  onSelect: vi.fn(),
  onHorizon: vi.fn(),
  showGrowthControl: false,
};
function view(values: Partial<Parameters<typeof SceneReview>[0]> = {}) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <SceneReview {...props} snapshot={snapshot()} horizon={0} {...values} />
    </QueryClientProvider>
  );
}
async function applyStreetCamera() {
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Выбрать уличный ракурс' }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Выбрать уличный ракурс' }),
  );
  const onApplied = scene.controller.beginStreetPlacement.mock.calls.at(-1)![0];
  act(() => onApplied());
  expect(
    screen.getByRole('button', { name: 'Выровнять горизонт' }),
  ).toBeVisible();
}
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it('keeps the applied street control during forecast loading and a new year snapshot', async () => {
  const current = snapshot();
  const test = render(view({ snapshot: current }));
  await applyStreetCamera();
  test.rerender(view({ snapshot: current, horizon: 1, loading: true }));
  expect(
    screen.getByRole('button', { name: 'Выровнять горизонт' }),
  ).toBeVisible();
  test.rerender(view({ snapshot: snapshot(1), horizon: 1 }));
  fireEvent.click(screen.getByRole('button', { name: 'Выровнять горизонт' }));
  expect(scene.controller.levelStreetHorizon).toHaveBeenCalledOnce();
  test.rerender(view({ snapshot: snapshot(), horizon: 0 }));
  expect(
    screen.getByRole('button', { name: 'Выровнять горизонт' }),
  ).toBeVisible();
});

it.each(['geometry', 'inactive'] as const)(
  'resets the applied street control when the %s basis changes',
  async (change) => {
    const test = render(view());
    await applyStreetCamera();
    test.rerender(
      view(
        change === 'geometry'
          ? { snapshot: { ...snapshot(), geometry_version: 3 } }
          : { active: false },
      ),
    );
    expect(
      screen.queryByRole('button', { name: 'Выровнять горизонт' }),
    ).toBeNull();
  },
);
