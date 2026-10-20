import { describe, expect, it } from 'vitest';
import {
  clampImageView,
  fitImageScale,
  zoomImageView,
} from './SceneRenderImageViewer.geometry';

describe('render image viewport geometry', () => {
  const image = { width: 1000, height: 800 };
  const viewport = { width: 400, height: 300 };

  it('fits both dimensions and never enlarges a small image', () => {
    expect(fitImageScale(image, viewport)).toBe(0.375);
    expect(fitImageScale({ width: 100, height: 80 }, viewport)).toBe(1);
    expect(
      clampImageView({ mode: 'fit', scale: 3, x: 800, y: -200 }, image, {
        width: 200,
        height: 400,
      }),
    ).toEqual({ mode: 'fit', scale: 0.2, x: 0, y: 0 });
  });

  it('clamps oversized axes to the edges while centering the axis that fits', () => {
    expect(
      clampImageView(
        { mode: 'custom', scale: 1, x: 900, y: -900 },
        image,
        viewport,
      ),
    ).toEqual({ mode: 'custom', scale: 1, x: 300, y: -250 });
    expect(
      clampImageView({ mode: 'custom', scale: 1, x: 900, y: -900 }, image, {
        width: 1200,
        height: 300,
      }),
    ).toEqual({ mode: 'custom', scale: 1, x: 0, y: -250 });
  });

  it('preserves the image point under a zoom anchor until an edge limits it', () => {
    const view = { mode: 'custom' as const, scale: 2, x: 20, y: -50 };
    const anchor = { x: 80, y: -30 };
    const next = zoomImageView(view, 4, anchor, image, viewport);
    expect((anchor.x - next.x) / next.scale).toBe(
      (anchor.x - view.x) / view.scale,
    );
    expect((anchor.y - next.y) / next.scale).toBe(
      (anchor.y - view.y) / view.scale,
    );
    expect(zoomImageView(view, 0.1, anchor, image, viewport)).toEqual({
      mode: 'custom',
      scale: 0.1,
      x: 0,
      y: 0,
    });
  });

  it('retains 100% on resize and recenters when the whole image fits', () => {
    expect(
      clampImageView({ mode: 'custom', scale: 1, x: 200, y: -120 }, image, {
        width: 1500,
        height: 1000,
      }),
    ).toEqual({ mode: 'custom', scale: 1, x: 0, y: 0 });
  });
});
