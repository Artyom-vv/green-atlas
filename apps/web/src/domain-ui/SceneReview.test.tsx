import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SceneSnapshot } from '@green/api-client';
import { SCENE_ACCESSIBILITY_CONTRACT } from './scene/sceneRenderContract';
import { SceneReview } from './SceneReview';

const sceneMocks = vi.hoisted(() => ({
  controllers: [] as Array<{
    updateSnapshot: ReturnType<typeof vi.fn>;
    updateIssues: ReturnType<typeof vi.fn>;
    updateSelection: ReturnType<typeof vi.fn>;
    setRootsVisible: ReturnType<typeof vi.fn>;
    setCameraMode: ReturnType<typeof vi.fn>;
    selectFromScene: (objectId: string) => void;
    dispose: ReturnType<typeof vi.fn>;
  }>,
  assetLibrary: { dispose: vi.fn() },
}));

vi.mock('./scene/plantAssets', () => ({
  PlantAssetLibrary: { load: vi.fn(async () => sceneMocks.assetLibrary) },
}));

vi.mock('./scene/SceneController', () => ({
  SceneController: class {
    updateSnapshot = vi.fn();
    updateIssues = vi.fn();
    updateSelection = vi.fn();
    setRootsVisible = vi.fn();
    setCameraMode = vi.fn();
    selectFromScene: (objectId: string) => void;
    loadedModelCount = vi.fn(() => 24);
    dispose = vi.fn();

    constructor(options: { onSelect: (objectId: string) => void }) {
      this.selectFromScene = options.onSelect;
      sceneMocks.controllers.push(this);
    }
  },
}));

class ResizeObserverMock {
  observe() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverMock);

const snapshot = (horizonYear = 0): SceneSnapshot => ({
  plan_version: 4,
  horizon_year: horizonYear,
  coordinate_origin: [1000, 2000],
  georeference_status: 'local',
  geometry_source: 'prepared_geometry',
  completeness: 'partial',
  terrain_status: 'missing',
  building_heights_status: 'missing',
  building_feature_count: 0,
  building_height_confirmed_count: 0,
  note: 'Упрощённая сцена.',
  data_gaps: ['Рельеф'],
  objects: [{
    object_id: 'tree-1', kind: 'tree', size_class: 'standard', growth_stage: horizonYear ? 'developing' : 'planting', growth_stage_status: 'estimated', forecast_horizon_year: horizonYear,
    local_x: 0, local_y: 0, crown_shape: 'round', canopy_radius_min_m: 1.6, canopy_radius_max_m: horizonYear ? 3.2 : 1.6,
    height_status: 'estimated', confidence: 'unknown', status: 'valid', locked: false,
  }],
});

beforeEach(() => {
  sceneMocks.controllers.length = 0;
  sceneMocks.assetLibrary.dispose.mockClear();
});

afterEach(cleanup);

describe('SceneReview', () => {
  it('labels model provenance and forwards arbitrary forecast horizons', async () => {
    const onHorizon = vi.fn();
    render(<SceneReview snapshot={snapshot()} horizon={0} selectedIds={['tree-1']} onHorizon={onHorizon} onSelect={vi.fn()} />);

    expect(screen.getByText(/Локальные координаты DXF/)).toBeVisible();
    expect(screen.getByText('DXF — источник')).toBeVisible();
    expect(screen.getByText('Выбрано: 1')).toBeVisible();
    await waitFor(() => expect(screen.queryByText('Загружаем модели растений')).not.toBeInTheDocument());
    fireEvent.input(screen.getByRole('slider', { name: 'Горизонт прогноза' }), { target: { value: '20' } });
    expect(onHorizon).toHaveBeenCalledWith(20);
  });

  it('updates selection, roots and forecast without recreating the scene controller', async () => {
    const onSelect = vi.fn();
    const view = render(<SceneReview snapshot={snapshot()} horizon={0} selectedIds={[]} onHorizon={vi.fn()} onSelect={onSelect} />);
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];
    expect(controller.updateSnapshot).toHaveBeenCalledWith(snapshot(), []);
    expect(controller.updateSelection).toHaveBeenCalledWith([]);
    controller.selectFromScene('tree-1');
    expect(onSelect).toHaveBeenCalledWith('tree-1');

    view.rerender(<SceneReview snapshot={snapshot()} horizon={0} selectedIds={['tree-1']} onHorizon={vi.fn()} onSelect={onSelect} />);
    await waitFor(() => expect(controller.updateSelection).toHaveBeenLastCalledWith(['tree-1']));
    expect(sceneMocks.controllers).toHaveLength(1);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Корни' }));
    await waitFor(() => expect(controller.setRootsVisible).toHaveBeenLastCalledWith(true));
    expect(sceneMocks.controllers).toHaveLength(1);

    const forecast = snapshot(20);
    view.rerender(<SceneReview snapshot={forecast} horizon={20} selectedIds={['tree-1']} onHorizon={vi.fn()} onSelect={onSelect} />);
    await waitFor(() => expect(controller.updateSnapshot).toHaveBeenLastCalledWith(forecast, []));
    expect(sceneMocks.controllers).toHaveLength(1);
  });

  it('exposes camera actions and explains the angle-independent outline', async () => {
    render(<SceneReview snapshot={snapshot()} horizon={0} selectedIds={['tree-1']} onHorizon={vi.fn()} onSelect={vi.fn()} />);
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];

    fireEvent.click(screen.getByRole('button', { name: SCENE_ACCESSIBILITY_CONTRACT.resetCameraLabel }));
    fireEvent.click(screen.getByRole('button', { name: SCENE_ACCESSIBILITY_CONTRACT.focusSelectionLabel }));
    expect(controller.setCameraMode).toHaveBeenNthCalledWith(1, 'overview');
    expect(controller.setCameraMode).toHaveBeenNthCalledWith(2, 'ground');
    expect(screen.getByLabelText(SCENE_ACCESSIBILITY_CONTRACT.outlineDescription)).toBeVisible();
    expect(screen.getByLabelText(SCENE_ACCESSIBILITY_CONTRACT.canvasLabel)).toBeVisible();
  });

  it('disposes the controller only when the mounted canvas leaves the document', async () => {
    const view = render(<SceneReview snapshot={snapshot()} horizon={0} selectedIds={[]} onHorizon={vi.fn()} onSelect={vi.fn()} />);
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];
    expect(controller.dispose).not.toHaveBeenCalled();
    view.unmount();
    expect(controller.dispose).toHaveBeenCalledTimes(1);
  });
});
