import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import {
  CanvasViewControls,
  type CanvasViewControlsProps,
} from './CanvasViewControls';
import {
  planComparisonPresentation,
  type PlanComparisonMode,
} from '@/entities/editor/model/planComparison';

afterEach(cleanup);
const project = {
  map_ready: true,
  plan: {},
  layers: [{ mapped_kind: 'lawn', visible: true, object_count: 1 }],
  import_status: { mode: 'autocad_live' },
} as unknown as CanvasViewControlsProps['project'];
function Fixture({
  blocked = false,
  draft = false,
  changeMapMode = vi.fn(),
  setPendingScene = vi.fn(),
}: {
  blocked?: boolean;
  draft?: boolean;
  changeMapMode?: (mode: '2d' | '3d') => void;
  setPendingScene?: CanvasViewControlsProps['setPendingScene'];
}) {
  const [mode, setMode] = useState<PlanComparisonMode>('editing');
  return (
    <CanvasViewControls
      project={project}
      sceneOpen={false}
      mapRenderMode="design"
      hasUnsavedWork={draft}
      setMapRenderMode={() => {}}
      setPendingScene={setPendingScene}
      changeMapMode={changeMapMode}
      planComparison={{
        mode,
        ...planComparisonPresentation(mode),
        canEnter: !blocked,
        change: setMode,
      }}
    />
  );
}
it('keeps the same controls mounted while toggling plan plantings without adding phase buttons', () => {
  render(<Fixture />);
  const toggle = screen.getByRole('button', {
    name: 'Показать без посадок плана',
  });
  const legend = screen.getByRole('button', { name: 'Легенда' });
  const view = screen.getByRole('button', {
    name: 'Подложка карты: проектный вид',
  });
  const threeD = screen.getByRole('button', { name: '3D' });
  const initialControls = screen.getAllByRole('button');
  const expectSameControls = () => {
    const controls = screen.getAllByRole('button');
    expect(controls).toHaveLength(initialControls.length);
    controls.forEach((control, index) => {
      expect(control).toBe(initialControls[index]);
    });
  };
  toggle.focus();
  fireEvent.click(toggle);
  expect(screen.getByRole('button', { name: 'Легенда' })).toBe(legend);
  expect(screen.getByRole('button', { name: '3D' })).toBe(threeD);
  expect(screen.getAllByRole('toolbar')).toHaveLength(1);
  expect(
    screen.getByRole('button', { name: 'Подложка карты: проектный вид' }),
  ).toBe(view);
  expectSameControls();
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
  expect(threeD).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'До' })).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'После' }),
  ).not.toBeInTheDocument();
  fireEvent.keyDown(toggle, { key: 'Home' });
  expect(view).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' });
  expect(legend).toHaveFocus();
  fireEvent.click(toggle);
  expect(
    screen.getByRole('button', { name: 'Показать без посадок плана' }),
  ).toBe(toggle);
  expectSameControls();
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  expect(threeD).toBeEnabled();
  expect(
    screen.getByRole('button', { name: 'Подложка карты: проектный вид' }),
  ).toBeVisible();
});
it('blocks entry during an operation and preserves the draft warning and 3D guard during comparison', () => {
  const changeMapMode = vi.fn();
  const { rerender } = render(
    <Fixture blocked draft changeMapMode={changeMapMode} />,
  );
  const toggle = screen.getByRole('button', {
    name: 'Показать без посадок плана',
  });
  expect(toggle).toBeDisabled();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  rerender(<Fixture draft changeMapMode={changeMapMode} />);
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('Черновик не включён в сравнение')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '3D' }));
  expect(changeMapMode).not.toHaveBeenCalled();
  rerender(<Fixture blocked draft changeMapMode={changeMapMode} />);
  expect(toggle).toBeEnabled();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  expect(
    screen.queryByText('Черновик не включён в сравнение'),
  ).not.toBeInTheDocument();
});

it('restores the normal dirty guard after returning from the view without plan plantings', () => {
  const changeMapMode = vi.fn();
  const setPendingScene = vi.fn();
  render(
    <Fixture
      draft
      changeMapMode={changeMapMode}
      setPendingScene={setPendingScene}
    />,
  );
  const toggle = screen.getByRole('button', {
    name: 'Показать без посадок плана',
  });
  const threeD = screen.getByRole('button', { name: '3D' });
  fireEvent.click(toggle);
  fireEvent.click(threeD);
  expect(setPendingScene).not.toHaveBeenCalled();
  fireEvent.click(toggle);
  fireEvent.click(threeD);
  expect(setPendingScene).toHaveBeenCalledWith(true);
  expect(changeMapMode).not.toHaveBeenCalled();
});
