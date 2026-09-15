import { controlFixture } from '@/test/controlFixture';
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkspaceScene, type WorkspaceSceneProps } from './WorkspaceScene';

const sceneModule = vi.hoisted(() => {
  let resolve!: () => void;
  const ready = new Promise<void>((complete) => {
    resolve = complete;
  });
  return { ready, resolve };
});

vi.mock('@/widgets/scene/ui/SceneReview', async () => {
  await sceneModule.ready;
  return {
    SceneReview: ({ active }: { active: boolean }) => (
      <section aria-label="Готовая 3D-сцена" hidden={!active}>
        <input aria-label="Состояние 3D" defaultValue="Камера" />
      </section>
    ),
  };
});

describe('lazy workspace scene', () => {
  it('releases the 2D surface immediately when the user returns before the 3D module loads', async () => {
    const props: WorkspaceSceneProps = {
      editorBusy: false,
      handleCoordinate: vi.fn(),
      issues: [],
      mapPanActive: false,
      planLocked: false,
      project: controlFixture().project,
      projectHasPlan: true,
      sceneHorizon: 0,
      sceneInitialViewState: undefined,
      sceneMounted: true,
      sceneOpen: true,
      // Only the read projection of the query is consumed by this surface.
      sceneQuery: {
        data: undefined,
        isLoading: false,
        isFetching: false,
        error: null,
      } as WorkspaceSceneProps['sceneQuery'],
      sceneRequestHorizon: 0,
      sceneReview: { current: null },
      sceneViewStateRef: { current: undefined },
      selectFromExplorer: vi.fn(),
      selectedIds: [],
      selectedLocked: false,
      selectedPatternZoneIds: [],
      setSceneHorizon: vi.fn(),
    };
    const { rerender } = render(<WorkspaceScene {...props} />);
    const loading = screen.getByText('Загрузка 3D');
    expect(loading).toBeVisible();

    rerender(<WorkspaceScene {...props} sceneOpen={false} />);
    expect(loading).not.toBeVisible();
    expect(loading.closest('[hidden]')).toBeInTheDocument();

    await act(async () => sceneModule.resolve());
    expect(screen.queryByText('Загрузка 3D')).toBeNull();
    const scene = screen.getByRole('region', { hidden: true });
    expect(scene).not.toBeVisible();

    rerender(<WorkspaceScene {...props} />);
    expect(screen.getByRole('region', { name: 'Готовая 3D-сцена' })).toBe(
      scene,
    );
    expect(scene).toBeVisible();
  });
});
