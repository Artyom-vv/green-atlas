import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SceneSnapshot } from '@green/api-client';
import {
  SCENE_ACCESSIBILITY_CONTRACT,
  SCENE_RESOURCE_TELEMETRY_ATTRIBUTES,
  SCENE_TELEMETRY_ATTRIBUTES,
  type SceneRenderTelemetry,
} from '@/widgets/scene/model/sceneRenderContract';
import { resetSceneTelemetryRegistryForTests } from '@/widgets/scene/adapters/three/sceneTelemetryRegistry';
import {
  SceneResourceDiagnostics,
  SCENE_RESOURCE_DIAGNOSTICS_LABEL,
} from '@/widgets/scene/ui/SceneResourceDiagnostics';
import {
  SceneReview,
  type SceneReviewHandle,
} from '@/widgets/scene/ui/SceneReview';
import { acquirePlantAssetLibrary } from '@/widgets/scene/adapters/three/plantAssets';

const sceneMocks = vi.hoisted(() => ({
  rendererFactory: vi.fn((_canvas?: unknown) => ({
    dispose: vi.fn(),
    forceContextLoss: vi.fn(),
  })),
  controllers: [] as Array<{
    updateSnapshot: ReturnType<typeof vi.fn>;
    updateIssues: ReturnType<typeof vi.fn>;
    updateSelection: ReturnType<typeof vi.fn>;
    setRootsVisible: ReturnType<typeof vi.fn>;
    setCameraMode: ReturnType<typeof vi.fn>;
    fitSelection: ReturnType<typeof vi.fn>;
    fitExtent: ReturnType<typeof vi.fn>;
    updateWorkZones: ReturnType<typeof vi.fn>;
    setRelocateSelection: ReturnType<typeof vi.fn>;
    importPlanViewState: ReturnType<typeof vi.fn>;
    exportPlanViewState: ReturnType<typeof vi.fn>;
    selectFromScene: (objectId: string) => void;
    emitTelemetry: (telemetry: SceneRenderTelemetry) => void;
    moveFromScene: (coordinate: [number, number]) => void;
    dispose: ReturnType<typeof vi.fn>;
  }>,
  assetLibrary: { dispose: vi.fn(), loadedModelCount: vi.fn(() => 4) },
}));

vi.mock('@/widgets/scene/adapters/three/plantAssets', () => ({
  acquirePlantAssetLibrary: vi.fn(() => ({
    library: Promise.resolve(sceneMocks.assetLibrary),
    release: vi.fn(),
  })),
}));

vi.mock('@/widgets/scene/adapters/three/SceneController', () => ({
  createSceneRenderer: (canvas: unknown) => sceneMocks.rendererFactory(canvas),
  SceneController: class {
    updateSnapshot = vi.fn();
    updateIssues = vi.fn();
    updateSelection = vi.fn();
    setRootsVisible = vi.fn();
    setCameraMode = vi.fn();
    fitSelection = vi.fn();
    fitExtent = vi.fn();
    updateWorkZones = vi.fn();
    zoom = vi.fn();
    setRelocateSelection = vi.fn();
    importPlanViewState = vi.fn();
    exportPlanViewState = vi.fn();
    selectFromScene: (objectId: string) => void;
    emitTelemetry: (telemetry: SceneRenderTelemetry) => void;
    moveFromScene: (coordinate: [number, number]) => void;
    loadedModelCount = vi.fn(() => 24);
    dispose = vi.fn();

    constructor(options: {
      onSelect: (objectId: string) => void;
      onMoveTarget?: (coordinate: [number, number]) => void;
      onTelemetry?: (telemetry: SceneRenderTelemetry) => void;
    }) {
      this.selectFromScene = options.onSelect;
      this.moveFromScene = (coordinate) => options.onMoveTarget?.(coordinate);
      this.emitTelemetry = (telemetry) => options.onTelemetry?.(telemetry);
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
  objects: [
    {
      object_id: 'tree-1',
      kind: 'tree',
      size_class: 'standard',
      growth_stage: horizonYear ? 'developing' : 'planting',
      growth_stage_status: 'estimated',
      forecast_horizon_year: horizonYear,
      local_x: 0,
      local_y: 0,
      crown_shape: 'round',
      canopy_radius_min_m: 1.6,
      canopy_radius_max_m: horizonYear ? 3.2 : 1.6,
      height_status: 'estimated',
      confidence: 'unknown',
      status: 'valid',
      locked: false,
    },
  ],
});

beforeEach(() => {
  resetSceneTelemetryRegistryForTests();
  sceneMocks.controllers.length = 0;
  sceneMocks.assetLibrary.dispose.mockClear();
  sceneMocks.assetLibrary.loadedModelCount.mockReset().mockReturnValue(4);
  vi.mocked(acquirePlantAssetLibrary).mockClear();
  sceneMocks.rendererFactory.mockReset();
  sceneMocks.rendererFactory.mockImplementation(() => ({
    dispose: vi.fn(),
    forceContextLoss: vi.fn(),
  }));
});

afterEach(cleanup);

describe('SceneReview', () => {
  it('applies the latest roots setting when models finish loading', async () => {
    let resolveModels!: (value: typeof sceneMocks.assetLibrary) => void;
    vi.mocked(acquirePlantAssetLibrary).mockReturnValueOnce({
      library: new Promise<typeof sceneMocks.assetLibrary>((resolve) => {
        resolveModels = resolve;
      }),
      release: vi.fn(),
    } as unknown as ReturnType<typeof acquirePlantAssetLibrary>);
    render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Данные и управление 3D' }),
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Показать корни' }));
    await act(async () => resolveModels(sceneMocks.assetLibrary));
    expect(sceneMocks.controllers[0].setRootsVisible).toHaveBeenLastCalledWith(
      true,
    );
  });

  it('releases a rejected model lease once and allows an explicit retry', async () => {
    const release = vi.fn();
    const report = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(acquirePlantAssetLibrary).mockReturnValueOnce({
      library: Promise.reject(new Error('models offline')),
      release,
    });
    const view = render(
      <SceneReview
        horizon={0}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    await screen.findByText('Не удалось загрузить 3D-сцену. 2D-план сохранён.');
    expect(release).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить запуск 3D' }),
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    view.unmount();
    expect(release).toHaveBeenCalledTimes(1);
    report.mockRestore();
  });

  it('does not attach a controller after a pending model lease outlives the canvas', async () => {
    let resolveModels!: (value: typeof sceneMocks.assetLibrary) => void;
    const release = vi.fn();
    vi.mocked(acquirePlantAssetLibrary).mockReturnValueOnce({
      library: new Promise<typeof sceneMocks.assetLibrary>((resolve) => {
        resolveModels = resolve;
      }),
      release,
    } as unknown as ReturnType<typeof acquirePlantAssetLibrary>);
    const view = render(
      <SceneReview
        horizon={0}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    view.unmount();
    await act(async () => resolveModels(sceneMocks.assetLibrary));
    expect(sceneMocks.controllers).toHaveLength(0);
    expect(release).toHaveBeenCalledTimes(1);
  });
  it('identifies the displayed forecast while a newer horizon is loading without changing the scene data', async () => {
    const available = snapshot(0),
      next = snapshot(40);
    const props = {
      selectedIds: ['tree-1'],
      onHorizon: vi.fn(),
      onSelect: vi.fn(),
    };
    const view = render(
      <SceneReview {...props} snapshot={available} horizon={0} />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];
    view.rerender(
      <SceneReview {...props} snapshot={available} horizon={40} loading />,
    );
    expect(
      screen.getByText('Обновляем прогноз').closest('[role="status"]'),
    ).toBeVisible();
    expect(
      screen.getByText('На сцене: сейчас. Запрошено: через 40 лет.'),
    ).toBeVisible();
    expect(screen.getByRole('slider')).toHaveValue('40');
    expect(controller.updateSnapshot).toHaveBeenLastCalledWith(available, []);
    view.rerender(<SceneReview {...props} snapshot={next} horizon={40} />);
    expect(screen.queryByText(/На сцене:/)).not.toBeInTheDocument();
    expect(controller.updateSnapshot).toHaveBeenLastCalledWith(next, []);
    expect(controller.updateSelection).toHaveBeenLastCalledWith(['tree-1']);
    expect(sceneMocks.controllers).toHaveLength(1);
  });

  it('retains the displayed year beside a request error even when the assistant owns the slider', async () => {
    const available = snapshot(20);
    const props = {
      snapshot: available,
      horizon: 40,
      selectedIds: [],
      onHorizon: vi.fn(),
      onSelect: vi.fn(),
      showGrowthControl: false,
    };
    const view = render(<SceneReview {...props} loading />);
    await screen.findByText('На сцене: через 20 лет. Запрошено: через 40 лет.');
    view.rerender(<SceneReview {...props} error="Сеть недоступна" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Сеть недоступна');
    expect(
      screen.getByText('На сцене: через 20 лет. Запрошено: через 40 лет.'),
    ).toBeVisible();
    expect(screen.queryByText('Обновляем прогноз')).not.toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(sceneMocks.controllers[0].updateSnapshot).toHaveBeenLastCalledWith(
      available,
      [],
    );
  });

  it('does not claim a displayed horizon when the first scene request has no snapshot', async () => {
    render(
      <SceneReview
        horizon={40}
        selectedIds={[]}
        error="Не удалось получить сцену"
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось получить сцену',
    );
    expect(screen.queryByText(/На сцене:/)).not.toBeInTheDocument();
    expect(sceneMocks.controllers[0].updateSnapshot).not.toHaveBeenCalled();
  });

  it('does not duplicate the shared project forecast control', () => {
    render(
      <SceneReview
        snapshot={snapshot(20)}
        horizon={20}
        showGrowthControl={false}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
  });
  it('focuses the visible 3D camera through inspector actions', async () => {
    const ref = createRef<SceneReviewHandle>();
    render(
      <SceneReview
        ref={ref}
        snapshot={snapshot()}
        horizon={0}
        selectedIds={['tree-1']}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    act(() => ref.current?.fitSelection());
    expect(sceneMocks.controllers[0].fitSelection).toHaveBeenCalled();
  });
  it('labels model provenance and forwards arbitrary forecast horizons', async () => {
    const onHorizon = vi.fn();
    render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={['tree-1']}
        onHorizon={onHorizon}
        onSelect={vi.fn()}
      />,
    );

    expect(
      screen.queryByText(/Локальные координаты DXF/),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Данные и управление 3D' }),
    );
    expect(screen.getByText(/Локальные координаты DXF/)).toBeVisible();
    expect(
      screen.getAllByText('Высоты не заданы', { selector: 'dd' }),
    ).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    await waitFor(() =>
      expect(
        screen.queryByText('Загружаем модели растений'),
      ).not.toBeInTheDocument(),
    );
    fireEvent.input(screen.getByRole('slider', { name: 'Горизонт прогноза' }), {
      target: { value: '20' },
    });
    expect(onHorizon).toHaveBeenCalledWith(20);
  });

  it('updates selection, roots and forecast without recreating the scene controller', async () => {
    const onSelect = vi.fn();
    const view = render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={onSelect}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];
    expect(controller.updateSnapshot).toHaveBeenCalledWith(snapshot(), []);
    expect(controller.updateSelection).toHaveBeenCalledWith([]);
    controller.selectFromScene('tree-1');
    expect(onSelect).toHaveBeenCalledWith('tree-1');

    view.rerender(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={['tree-1']}
        onHorizon={vi.fn()}
        onSelect={onSelect}
      />,
    );
    await waitFor(() =>
      expect(controller.updateSelection).toHaveBeenLastCalledWith(['tree-1']),
    );
    expect(sceneMocks.controllers).toHaveLength(1);

    fireEvent.click(
      screen.getByRole('button', { name: 'Данные и управление 3D' }),
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Показать корни' }));
    await waitFor(() =>
      expect(controller.setRootsVisible).toHaveBeenLastCalledWith(true),
    );
    expect(sceneMocks.controllers).toHaveLength(1);

    const forecast = snapshot(20);
    view.rerender(
      <SceneReview
        snapshot={forecast}
        horizon={20}
        selectedIds={['tree-1']}
        onHorizon={vi.fn()}
        onSelect={onSelect}
      />,
    );
    await waitFor(() =>
      expect(controller.updateSnapshot).toHaveBeenLastCalledWith(forecast, []),
    );
    expect(sceneMocks.controllers).toHaveLength(1);
  });

  it('exposes camera actions and explains the angle-independent outline', async () => {
    render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={['tree-1']}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];

    fireEvent.click(
      screen.getByRole('button', { name: 'Показать весь чертёж в 3D' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Показать выбранные посадки' }),
    );
    expect(controller.setCameraMode).toHaveBeenNthCalledWith(1, 'overview');
    expect(controller.fitSelection).toHaveBeenCalledOnce();
    fireEvent.click(
      screen.getByRole('button', { name: 'Данные и управление 3D' }),
    );
    expect(screen.getByText(/Синий контур — выбор/)).toBeVisible();
    expect(
      screen.getByLabelText(SCENE_ACCESSIBILITY_CONTRACT.canvasLabel),
    ).toBeVisible();
  });

  it('does not advertise a separate 3D move without an on-canvas preview', async () => {
    const onMoveTarget = vi.fn();
    render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={['tree-1']}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
        onMoveTarget={onMoveTarget}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];

    expect(
      screen.queryByRole('button', { name: 'Переместить выбранное в 3D' }),
    ).not.toBeInTheDocument();
    controller.moveFromScene([1012, 2034]);

    expect(onMoveTarget).toHaveBeenCalledWith([1012, 2034]);
  });

  it('publishes only measured frame, quality and renderer resource counters', async () => {
    const diagnosticsView = render(<SceneResourceDiagnostics />);
    const sceneView = render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const scene = screen.getByRole('region', {
      name: 'Параметрический 3D-предпросмотр',
    });
    expect(scene).toHaveAttribute(
      SCENE_TELEMETRY_ATTRIBUTES.status,
      'warming-up',
    );
    expect(scene).toHaveAttribute(SCENE_TELEMETRY_ATTRIBUTES.fps, '0');
    expect(scene).toHaveAttribute(SCENE_TELEMETRY_ATTRIBUTES.geometries, '0');

    sceneMocks.controllers[0].emitTelemetry({
      rendererGeneration: 3,
      fps: 57,
      drawCalls: 41,
      triangles: 712_340,
      renderScale: 0.85,
      effectivePixelRatio: 1.275,
      outlineWidthBufferPx: 2.55,
      geometries: 19,
      textures: 6,
      programs: 8,
      loadedPlantModels: 12,
      plantInstances: 1,
      visiblePlantInstances: 1,
    });

    await waitFor(() =>
      expect(scene).toHaveAttribute(SCENE_TELEMETRY_ATTRIBUTES.status, 'ready'),
    );
    expect(scene).toHaveAttribute(SCENE_TELEMETRY_ATTRIBUTES.fps, '57');
    expect(scene).toHaveAttribute(SCENE_TELEMETRY_ATTRIBUTES.drawCalls, '41');
    expect(scene).toHaveAttribute(
      SCENE_TELEMETRY_ATTRIBUTES.triangles,
      '712340',
    );
    expect(scene).toHaveAttribute(
      SCENE_TELEMETRY_ATTRIBUTES.renderScale,
      '0.85',
    );
    expect(scene).toHaveAttribute(
      SCENE_TELEMETRY_ATTRIBUTES.effectivePixelRatio,
      '1.275',
    );
    expect(scene).toHaveAttribute(
      SCENE_TELEMETRY_ATTRIBUTES.outlineWidthBufferPx,
      '2.55',
    );

    const diagnostics = screen.getByLabelText(SCENE_RESOURCE_DIAGNOSTICS_LABEL);
    expect(diagnostics).toHaveAttribute('aria-hidden', 'true');
    expect(diagnostics).not.toBeVisible();
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.activeRenderers,
      '1',
    );
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.createdRenderers,
      '1',
    );
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.geometries,
      '19',
    );
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.textures,
      '6',
    );
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.programs,
      '8',
    );

    sceneView.unmount();
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.activeRenderers,
      '0',
    );
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.disposedRenderers,
      '1',
    );
    expect(diagnostics).toHaveAttribute(
      SCENE_RESOURCE_TELEMETRY_ATTRIBUTES.geometries,
      '0',
    );
    diagnosticsView.unmount();
  });

  it('disposes the controller only when the mounted canvas leaves the document', async () => {
    const view = render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    const controller = sceneMocks.controllers[0];
    expect(controller.dispose).not.toHaveBeenCalled();
    view.unmount();
    expect(controller.dispose).toHaveBeenCalledTimes(1);
  });

  it('forwards a changed 2D viewport to an already mounted 3D controller', async () => {
    const initialViewState = {
      center: [1120, 2170] as [number, number],
      resolution: 0.8,
      rotation: 0.2,
      viewport: [900, 600] as [number, number],
    };
    const view = render(
      <SceneReview
        active={false}
        snapshot={snapshot()}
        horizon={0}
        selectedIds={[]}
        initialViewState={initialViewState}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    expect(
      sceneMocks.controllers[0].importPlanViewState,
    ).not.toHaveBeenCalled();
    view.rerender(
      <SceneReview
        active
        snapshot={snapshot()}
        horizon={0}
        selectedIds={[]}
        initialViewState={initialViewState}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(sceneMocks.controllers[0].importPlanViewState).toHaveBeenCalledWith(
      initialViewState,
    );
  });

  it('balances controller creation and disposal through 20 simulated 2D↔3D cycles', async () => {
    for (let cycle = 0; cycle < 20; cycle += 1) {
      const view = render(
        <SceneReview
          snapshot={snapshot()}
          horizon={0}
          selectedIds={[]}
          onHorizon={vi.fn()}
          onSelect={vi.fn()}
        />,
      );
      await waitFor(() =>
        expect(sceneMocks.controllers).toHaveLength(cycle + 1),
      );
      const controller = sceneMocks.controllers[cycle];
      expect(controller.dispose).not.toHaveBeenCalled();
      view.unmount();
      expect(controller.dispose).toHaveBeenCalledTimes(1);
    }

    expect(sceneMocks.controllers).toHaveLength(20);
    expect(
      sceneMocks.controllers.every(
        (controller) => controller.dispose.mock.calls.length === 1,
      ),
    ).toBe(true);
  });

  it('lets the operator retry after a transient WebGL context failure', async () => {
    sceneMocks.rendererFactory.mockImplementationOnce(() => {
      throw new Error('WebGL context temporarily unavailable');
    });
    render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={[]}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    const retry = await screen.findByRole('button', {
      name: 'Повторить запуск 3D',
    });
    expect(
      screen.getByText(
        '3D недоступен в этом браузере. План остаётся доступен в 2D.',
      ),
    ).toBeVisible();
    fireEvent.click(retry);

    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    expect(sceneMocks.rendererFactory).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByRole('button', { name: 'Повторить запуск 3D' }),
    ).not.toBeInTheDocument();
  });

  it('acquires models again only after an explicit retry when the first library was empty', async () => {
    sceneMocks.assetLibrary.loadedModelCount.mockReturnValueOnce(0);
    render(
      <SceneReview
        snapshot={snapshot()}
        horizon={0}
        selectedIds={['tree-1']}
        onHorizon={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    const retry = await screen.findByRole('button', {
      name: 'Повторить запуск 3D',
    });
    expect(sceneMocks.controllers).toHaveLength(0);
    expect(acquirePlantAssetLibrary).toHaveBeenCalledOnce();
    fireEvent.click(retry);
    await waitFor(() => expect(sceneMocks.controllers).toHaveLength(1));
    expect(acquirePlantAssetLibrary).toHaveBeenCalledTimes(2);
    expect(sceneMocks.controllers[0].updateSnapshot).toHaveBeenLastCalledWith(
      snapshot(),
      [],
    );
    expect(sceneMocks.controllers[0].updateSelection).toHaveBeenLastCalledWith([
      'tree-1',
    ]);
    expect(
      screen.queryByRole('button', { name: 'Повторить запуск 3D' }),
    ).not.toBeInTheDocument();
  });
});
