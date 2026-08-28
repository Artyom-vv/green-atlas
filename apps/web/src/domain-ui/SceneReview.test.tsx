import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneReview } from './SceneReview';

vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  return {
    ...actual,
    WebGLRenderer: class {
      domElement: HTMLCanvasElement;
      constructor({ canvas }: { canvas: HTMLCanvasElement }) { this.domElement = canvas; }
      setPixelRatio() {}
      setClearColor() {}
      setSize() {}
      render() {}
      dispose() {}
    },
  };
});

class ResizeObserverMock {
  observe() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverMock);

afterEach(cleanup);

describe('SceneReview', () => {
  it('labels the model as partial and changes horizons without changing object identity', () => {
    const onHorizon = vi.fn();
    const onClose = vi.fn();
    render(<SceneReview snapshot={{
      plan_version: 4,
      horizon_year: 0,
      coordinate_origin: [1000, 2000],
      completeness: 'partial',
      terrain_status: 'missing',
      building_heights_status: 'missing',
      note: 'Упрощённая сцена.',
      data_gaps: ['Рельеф'],
      objects: [{ object_id: 'tree-1', kind: 'tree', local_x: 0, local_y: 0, crown_shape: 'round', canopy_radius_min_m: 1.6, canopy_radius_max_m: 1.6, confidence: 'unknown' }],
    }} horizon={0} selectedIds={['tree-1']} onHorizon={onHorizon} onSelect={vi.fn()} onClose={onClose} />);

    expect(screen.getByText('Упрощённая параметрическая сцена')).toBeVisible();
    expect(screen.getByText('Рельеф и высоты зданий не заданы')).toBeVisible();
    expect(screen.getByText('Выбрано: 1')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '20 лет' }));
    expect(onHorizon).toHaveBeenCalledWith(20);
    fireEvent.click(screen.getByRole('button', { name: 'Вернуться к карте' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
