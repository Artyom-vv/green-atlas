import { createRequire } from 'node:module';
import { apply, compose, create } from 'ol/transform';
import { describe, expect, it, vi } from 'vitest';
import { getReadyInfo, renderCadFrame, validateUnitScale } from './camera';
import type { CadViewer } from './sdkTypes';

// Use the SDK's own dependency, not the application's other Three version.
const sdkRequire = createRequire(
  createRequire(import.meta.url).resolve('dxf-viewer'),
);
const { OrthographicCamera, Vector3 } = sdkRequire(
  'three',
) as typeof import('three');

function fixture() {
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0.1, 2);
  const origin = { x: 2_100_000.125, y: -5_700_000.875 };
  const viewer: CadViewer = {
    HasRenderer: () => true,
    GetCanvas: () => document.createElement('canvas'),
    GetCamera: () => camera,
    GetOrigin: () => origin,
    GetBounds: () => ({ minX: 100, minY: 200, maxX: 1100, maxY: 2200 }),
    GetLayers: () => [],
    GetRenderer: () => ({
      info: { render: { calls: 12 } },
      getContext: vi.fn(),
    }),
    GetScene: () => ({ children: [1, 2] }),
    SetSize: vi.fn(),
    ShowLayers: vi.fn(),
    Render: vi.fn(),
    Destroy: vi.fn(),
    Load: vi.fn(),
  };
  return { viewer, camera, origin };
}

describe('CAD metre/SDK camera boundary', () => {
  it.each([1, 0.001, 0.0254])('matches OL XY at source scale %s', (scale) => {
    const { viewer, camera, origin } = fixture();
    const dimensions = [1, 1];
    for (const [width, height, resolution, rotation] of [
      [1024, 600, 5, 0],
      [1024, 600, 0.001, 0],
      [640, 900, 1.5, 0],
      [640, 900, 1.5, Math.PI / 3],
    ]) {
      const center = [(origin.x + 63.5) * scale, (origin.y - 91.25) * scale];
      renderCadFrame(
        viewer,
        { size: [width, height], viewState: { center, resolution, rotation } },
        scale,
        dimensions,
      );
      const transform = compose(
        create(),
        width / 2,
        height / 2,
        1 / resolution,
        -1 / resolution,
        -rotation,
        -center[0],
        -center[1],
      );
      for (const [dx, dy] of [
        [0, 0],
        [-30, 50],
        [70, -22],
      ]) {
        const xy = [center[0] + dx * resolution, center[1] + dy * resolution];
        const ol = apply(transform, [...xy]);
        const ndc = new Vector3(
          xy[0] / scale - origin.x,
          xy[1] / scale - origin.y,
          0,
        ).project(camera);
        const cad = [((ndc.x + 1) * width) / 2, ((1 - ndc.y) * height) / 2];
        expect(Math.hypot(cad[0] - ol[0], cad[1] - ol[1])).toBeLessThan(
          0.00001,
        );
      }
    }
    expect(viewer.Render).toHaveBeenCalledTimes(4);
    expect(viewer.SetSize).toHaveBeenCalledTimes(2);
  });

  it('scales source bounds once and exposes bounded readiness metrics', () => {
    const { viewer, origin } = fixture();
    expect(getReadyInfo(viewer, 0.001)).toMatchObject({
      boundsM: [0.1, 0.2, 1.1, 2.2],
      originM: [origin.x * 0.001, origin.y * 0.001],
      renderCalls: 12,
      sceneObjects: 2,
    });
  });

  it.each([0, -1, NaN, Infinity])('rejects invalid unit scale %s', (scale) => {
    expect(() => validateUnitScale(scale)).toThrow();
  });

  it('rejects non-finite source coordinates before readiness', () => {
    const { viewer } = fixture();
    viewer.GetBounds = () => ({ minX: 0, minY: 0, maxX: Infinity, maxY: 1 });
    expect(() => getReadyInfo(viewer, 1)).toThrow();
  });
});
