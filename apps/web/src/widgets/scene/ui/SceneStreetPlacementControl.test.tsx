import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SceneStreetPlacementControl } from './SceneStreetPlacementControl';

afterEach(cleanup);

it('requires a selected position and direction before explicit confirmation', () => {
  const onConfirm = vi.fn();
  const onRestart = vi.fn();
  const onCancel = vi.fn();
  const props = { onConfirm, onRestart, onCancel };
  const { rerender } = render(
    <SceneStreetPlacementControl
      {...props}
      state={{ step: 'position', snapped: false }}
    />,
  );
  expect(
    screen.getByRole('button', { name: 'Применить ракурс' }),
  ).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Другая точка' })).toBeNull();
  rerender(
    <SceneStreetPlacementControl
      {...props}
      state={{ step: 'direction', position: [0, 0], snapped: false }}
    />,
  );
  expect(
    screen.getByRole('button', { name: 'Применить ракурс' }),
  ).toBeDisabled();
  rerender(
    <SceneStreetPlacementControl
      {...props}
      state={{
        step: 'ready',
        position: [0, 0],
        target: [10, 0],
        snapped: true,
      }}
    />,
  );
  expect(
    screen.getByText('Направление привязано к осям чертежа'),
  ).toBeVisible();
  screen.getByRole('button', { name: 'Применить ракурс' }).click();
  screen.getByRole('button', { name: 'Другая точка' }).click();
  screen.getByRole('button', { name: 'Отменить' }).click();
  expect(onConfirm).toHaveBeenCalledOnce();
  expect(onRestart).toHaveBeenCalledOnce();
  expect(onCancel).toHaveBeenCalledOnce();
});
