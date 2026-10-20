import { projectFixture } from '@/test/projectFixture';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkspaceScene, type WorkspaceSceneProps } from './WorkspaceScene';
import {
  SceneActivityProvider,
  useSceneActivity,
} from '@/widgets/scene/model/SceneActivityContext';

function HeaderSlots() {
  const activity = useSceneActivity();
  return (
    <>
      <div data-testid="header" ref={activity?.setHeaderSlot} />
      <div data-testid="progress" ref={activity?.setProgressSlot} />
    </>
  );
}

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
      focusZones: vi.fn(),
      selectedPatternZones: [],
      handleCoordinate: vi.fn(),
      issues: [],
      mapPanActive: false,
      planLocked: false,
      project: projectFixture(),
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
    const returnToMap = vi.fn();
    const content = (sceneOpen = true) => (
      <SceneActivityProvider>
        <HeaderSlots />
        <WorkspaceScene
          {...props}
          sceneOpen={sceneOpen}
          viewControls={<button onClick={returnToMap}>2D</button>}
        />
      </SceneActivityProvider>
    );
    const { rerender } = render(content());
    const loading = screen.getByText('Открываем 3D');
    expect(loading).toBeVisible();
    expect(screen.getByTestId('header')).toContainElement(loading);
    expect(document.querySelector('[data-slot=viewport-loading]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '2D' }));
    expect(returnToMap).toHaveBeenCalledOnce();

    rerender(content(false));
    expect(screen.getByTestId('header')).toBeEmptyDOMElement();
    expect(screen.getByTestId('progress')).toBeEmptyDOMElement();
    expect(screen.queryByText('Открываем 3D')).not.toBeInTheDocument();

    await act(async () => sceneModule.resolve());
    expect(screen.queryByText('Открываем 3D')).toBeNull();
    const scene = screen.getByRole('region', { hidden: true });
    expect(scene).not.toBeVisible();

    rerender(content());
    expect(screen.getByRole('region', { name: 'Готовая 3D-сцена' })).toBe(
      scene,
    );
    expect(scene).toBeVisible();
  });
});
