import type { SceneRenderPreparation } from '@green/api-client';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneEnvironmentControl } from './SceneEnvironmentControl';

afterEach(cleanup);

const preparation: SceneRenderPreparation = {
  id: 'environment-for-year-20',
  sha256: 'b'.repeat(64),
  project_id: 'project',
  scene_sha256: 'a'.repeat(64),
  horizon_year: 20,
  geometry_version: 1,
  plan_version: 2,
  camera: {
    position: [20, 1.7, 30],
    quaternion: [0, 0, 0, 1],
    fov_y_deg: 42,
    aspect: 1.5,
    near: 0.04,
    far: 2000,
  },
  preview: {
    asset_version: 0,
    meshes: [],
    curbs: [],
    context_trees: [],
    suppressed_source_ids: [],
  },
  summary: {
    message: 'Сведения подготовлены для выбранного ракурса',
    context_tree_count: 247,
    map_building_count: 17,
    map_status: 'unaligned',
    conditional_building_count: 115,
    osm_building_count: 11,
    terrain_status: 'missing',
  },
  provenance: {},
};

async function openEnvironment() {
  fireEvent.click(screen.getByRole('button', { name: /^3D:/ }));
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: '3D' })).toBeVisible(),
  );
}

describe('SceneEnvironmentControl', () => {
  it('withholds an old preparation until the owner marks the requested scene ready', async () => {
    const onRefresh = vi.fn();
    const props = {
      preparation,
      loading: false,
      applying: false,
      onRefresh,
      onData: vi.fn(),
    };
    const view = render(
      <SceneEnvironmentControl {...props} canRefresh={false} />,
    );
    await openEnvironment();

    expect(screen.getByText('Ожидает готовности текущей сцены')).toBeVisible();
    expect(
      screen.queryAllByText('3D готов')[0] || null,
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Деревья окружения')).not.toBeInTheDocument();
    const refresh = screen.getByRole('button', {
      name: 'Обновить 3D',
    });
    expect(refresh).toBeDisabled();
    fireEvent.click(refresh);
    expect(onRefresh).not.toHaveBeenCalled();

    view.rerender(<SceneEnvironmentControl {...props} canRefresh />);
    expect(
      screen
        .getAllByText('3D готов')
        .find((element) => element.tagName === 'P')!,
    ).toBeVisible();
    expect(
      screen.getByText('Деревья окружения').nextElementSibling,
    ).toHaveTextContent('247');
    expect(
      screen.getByText('Высоты из OSM').nextElementSibling,
    ).toHaveTextContent('11');
    fireEvent.click(refresh);
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it.each([
    {
      loading: true,
      applying: false,
      message: 'Открываем 3D',
    },
    {
      loading: false,
      applying: true,
      message: 'Открываем 3D',
    },
  ])(
    'keeps details accessible while $message',
    async ({ loading, applying, message }) => {
      const onRefresh = vi.fn();
      const onData = vi.fn();
      render(
        <SceneEnvironmentControl
          preparation={preparation}
          canRefresh
          loading={loading}
          applying={applying}
          onRefresh={onRefresh}
          onData={onData}
        />,
      );
      const trigger = screen.getByRole('button', {
        name: /^3D:/,
      });
      expect(trigger).toBeEnabled();
      await openEnvironment();
      expect(
        screen
          .getAllByText(message)
          .find((element) => element.tagName === 'P')!,
      ).toBeVisible();
      expect(
        screen.queryAllByText('3D готов')[0] || null,
      ).not.toBeInTheDocument();
      expect(screen.getByText('На сцене: предыдущее окружение')).toBeVisible();
      expect(
        screen.getByText('Деревья окружения').nextElementSibling,
      ).toHaveTextContent('247');
      expect(screen.getByRole('button', { name: /^3D:/ })).not.toHaveAttribute(
        'aria-valuenow',
      );
      const refresh = screen.getByRole('button', {
        name: 'Обновить 3D',
      });
      expect(refresh).toBeDisabled();
      fireEvent.click(refresh);
      expect(onRefresh).not.toHaveBeenCalled();

      fireEvent.click(
        screen.getByRole('button', { name: 'Данные и управление 3D' }),
      );
      expect(onData).toHaveBeenCalledOnce();
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
    },
  );

  it('offers an explicit retry and identifies retained counts as the previous environment', async () => {
    const onRefresh = vi.fn();
    render(
      <SceneEnvironmentControl
        preparation={preparation}
        canRefresh
        loading={false}
        applying={false}
        error="Не удалось получить окружение"
        onRefresh={onRefresh}
        onData={vi.fn()}
      />,
    );
    await openEnvironment();
    expect(screen.getByText('Не удалось обновить окружение')).toBeVisible();
    expect(screen.getByText('На сцене: предыдущее окружение')).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось получить окружение',
    );
    expect(
      screen.queryAllByText('3D готов')[0] || null,
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('Деревья окружения').nextElementSibling,
    ).toHaveTextContent('247');
    expect(screen.getByText('Рендер доступен.')).toBeVisible();
    const retry = screen.getByRole('button', {
      name: 'Повторить загрузку 3D',
    });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it('explains a cold network failure without claiming a previous scene exists', async () => {
    render(
      <SceneEnvironmentControl
        canRefresh
        loading={false}
        applying={false}
        error="Failed to fetch"
        onRefresh={vi.fn()}
        onData={vi.fn()}
      />,
    );
    await openEnvironment();
    expect(screen.getByText('Окружение недоступно')).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось связаться с сервисом. Повторите попытку.',
    );
    expect(
      screen.queryByText(/Показано предыдущее окружение/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Failed to fetch')).not.toBeInTheDocument();
  });

  it('keeps the preparation message behind disclosure and preserves the location action', async () => {
    const onLocation = vi.fn();
    render(
      <SceneEnvironmentControl
        preparation={preparation}
        canRefresh
        loading={false}
        applying={false}
        onRefresh={vi.fn()}
        onData={vi.fn()}
        onLocation={onLocation}
      />,
    );
    await openEnvironment();
    expect(screen.queryByText(preparation.summary.message)).not.toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения о подготовке' }),
    );
    expect(screen.getByText(preparation.summary.message)).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Местоположение проекта' }),
    );
    expect(onLocation).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: /^3D:/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('does not fabricate a count or timer while cold preparation is pending', async () => {
    render(
      <SceneEnvironmentControl
        canRefresh
        loading
        applying={false}
        onRefresh={vi.fn()}
        onData={vi.fn()}
      />,
    );
    await openEnvironment();
    expect(screen.getByRole('button', { name: /^3D:/ })).not.toHaveAttribute(
      'aria-valuenow',
    );
    expect(
      screen.queryByRole('region', { name: 'Состав показанного окружения' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Прошло/)).not.toBeInTheDocument();
  });
});
