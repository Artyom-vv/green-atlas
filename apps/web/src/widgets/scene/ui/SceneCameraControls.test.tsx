import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneCameraControls } from './SceneCameraControls';

describe('SceneCameraControls', () => {
  afterEach(cleanup);

  it('keeps zoom, scene overview, selected zones and selected plantings as distinct camera actions', () => {
    const onZoom = vi.fn();
    const onOverview = vi.fn();
    const onFitZones = vi.fn();
    const onFitSelection = vi.fn();
    render(
      <SceneCameraControls
        hasSelection
        onZoom={onZoom}
        onOverview={onOverview}
        onFitSelection={onFitSelection}
        onFitZones={onFitZones}
        zoneCount={3}
      />,
    );

    screen.getByRole('button', { name: 'Приблизить 3D' }).click();
    screen.getByRole('button', { name: 'Отдалить 3D' }).click();
    screen.getByRole('button', { name: 'Показать весь чертёж в 3D' }).click();
    screen
      .getByRole('button', { name: 'Показать выбранные участки (3)' })
      .click();
    screen.getByRole('button', { name: 'Показать выбранные посадки' }).click();

    expect(onZoom.mock.calls).toEqual([[0.8], [1.25]]);
    expect(onOverview).toHaveBeenCalledOnce();
    expect(onFitZones).toHaveBeenCalledOnce();
    expect(onFitSelection).toHaveBeenCalledOnce();
  });

  it('preserves selection and street readiness gates without disabling available navigation', () => {
    const onStreet = vi.fn();
    const { rerender } = render(
      <SceneCameraControls
        hasSelection={false}
        streetDisabled
        onZoom={vi.fn()}
        onOverview={vi.fn()}
        onFitSelection={vi.fn()}
        onFitZones={vi.fn()}
        zoneCount={0}
        onStreet={onStreet}
      />,
    );
    expect(screen.getByRole('button', { name: 'Приблизить 3D' })).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Показать выбранные посадки' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Выбрать уличный ракурс' }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: /Показать выбранные участки/ }),
    ).toBeNull();

    rerender(
      <SceneCameraControls
        hasSelection
        streetPicking
        onZoom={vi.fn()}
        onOverview={vi.fn()}
        onFitSelection={vi.fn()}
        onStreet={onStreet}
      />,
    );
    const cancel = screen.getByRole('button', {
      name: 'Отменить выбор уличного ракурса',
    });
    expect(cancel).toHaveAttribute('aria-pressed', 'true');
    cancel.click();
    expect(onStreet).toHaveBeenCalledOnce();
  });
});
