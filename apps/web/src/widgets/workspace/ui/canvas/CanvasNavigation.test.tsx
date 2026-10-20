import { projectFixture } from '@/test/projectFixture';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CanvasNavigation } from './CanvasNavigation';

describe('CanvasNavigation', () => {
  afterEach(cleanup);

  it('focuses the selected zones from the navigation group and hides the action when the selection clears', () => {
    const project = projectFixture();
    const focusZones = vi.fn();
    const props = {
      project,
      focusZones,
      selectedPatternZones: project.planting_zones ?? [],
      mapViewport: { current: null },
      sceneOpen: false,
    };
    const { rerender } = render(<CanvasNavigation {...props} />);
    const focus = screen.getByRole('button', {
      name: 'Показать выбранные участки (1)',
    });
    expect(focus.closest('[role="toolbar"]')).toBe(
      screen.getByRole('toolbar', { name: 'Масштаб карты' }),
    );
    focus.click();
    expect(focusZones).toHaveBeenCalledWith(props.selectedPatternZones);

    rerender(<CanvasNavigation {...props} selectedPatternZones={[]} />);
    expect(
      screen.queryByRole('button', { name: /Показать выбранные участки/ }),
    ).toBeNull();
    rerender(<CanvasNavigation {...props} sceneOpen />);
    expect(screen.queryByRole('toolbar')).toBeNull();
  });
});
