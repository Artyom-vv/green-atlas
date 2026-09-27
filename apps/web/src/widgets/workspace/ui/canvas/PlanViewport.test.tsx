import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PlanViewport, type PlanViewportProps } from './PlanViewport';

vi.mock('@/widgets/map/ui/MapViewport', () => ({
  MapViewport: () => (
    <div role="region" aria-label="Карта проекта" tabIndex={0}>
      <input aria-label="Состояние рендера" defaultValue="Начальная камера" />
    </div>
  ),
}));
vi.mock('../../model/viewport/viewportDrawing', () => ({
  viewportDrawingBindings: () => ({}),
}));
vi.mock('../../model/viewport/viewportRow', () => ({
  viewportRowBindings: () => ({}),
}));
vi.mock('../../model/viewport/viewportBrush', () => ({
  viewportBrushBindings: () => ({}),
}));
vi.mock('../../model/viewport/viewportSelection', () => ({
  viewportSelectionBindings: () => ({}),
}));
vi.mock('../../model/viewport/viewportPresentation', () => ({
  viewportPresentationBindings: () => ({}),
}));

describe('plan viewport activation', () => {
  it('removes the inactive map from navigation while preserving its host and renderer state', () => {
    // The adapter bindings are outside this host lifecycle regression.
    const props = {
      projectId: 'project',
      mapViewport: { current: null },
      sceneOpen: false,
    } as PlanViewportProps;
    const { rerender } = render(<PlanViewport {...props} />);
    const map = screen.getByRole('region', { name: 'Карта проекта' });
    const host = map.parentElement!;
    const state = screen.getByRole('textbox', { name: 'Состояние рендера' });
    fireEvent.change(state, { target: { value: 'Сохранённая камера' } });

    rerender(<PlanViewport {...props} sceneOpen />);
    expect(screen.queryByRole('region', { name: 'Карта проекта' })).toBeNull();
    expect(host).toHaveAttribute('inert');
    expect(host).toHaveAttribute('aria-hidden', 'true');
    expect(host).not.toHaveAttribute('hidden');
    expect(host).toBeVisible();
    expect(map).toBeInTheDocument();

    rerender(<PlanViewport {...props} />);
    expect(screen.getByRole('region', { name: 'Карта проекта' })).toBe(map);
    expect(map.parentElement).toBe(host);
    expect(host).not.toHaveAttribute('inert');
    expect(state).toHaveValue('Сохранённая камера');
  });
});
