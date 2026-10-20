import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createOverviewCanvas } from './sourceOverviewCanvas';

const context = {
  setTransform: vi.fn(),
  fill: vi.fn(),
  stroke: vi.fn(),
  lineWidth: 0,
  lineJoin: '',
  fillStyle: '',
  strokeStyle: '',
  globalAlpha: 1,
};
const markup =
  '<svg><g data-kind="lawn" data-shape="area" fill="#123456" stroke="#123456"><path d="M0,0L10,0L10,-10Z M2,-2L3,-2L3,-3Z"/><path d="M10000,10000L10010,10000L10010,10010Z"/></g></svg>';

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    'Path2D',
    class {
      constructor(public d: string) {}
    },
  );
  vi.stubGlobal('devicePixelRatio', 2);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    context as unknown as CanvasRenderingContext2D,
  );
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('local vector canvas overview', () => {
  it('passes only supported paths and colours to the canvas', () => {
    const drawing = createOverviewCanvas(
      '<svg><g fill="url(https://example.com)" stroke="#123456"><path d="M0,0L1,1" onclick="alert(1)"/><path d="M0,0Q1,1,2,2"/><script>alert(1)</script></g></svg>',
      'cad',
    );
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30]);
    expect(context.fill).not.toHaveBeenCalled();
    expect(context.stroke).toHaveBeenCalledTimes(1);
    expect(context.stroke.mock.calls[0][0].d).toBe('M0,0L1,1');
    expect(context.strokeStyle).toBe('#123456');
  });
  it('draws retained vector paths with constant screen strokes at each zoom', () => {
    const drawing = createOverviewCanvas(markup);
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30]);
    expect(context.lineWidth).toBe(0.8);
    expect(context.setTransform).toHaveBeenLastCalledWith(2, 0, -0, 2, 40, 60);
    expect(drawing.element.width).toBe(1600);
    expect(drawing.element.querySelectorAll('path')).toHaveLength(0);
    expect(context.fill).toHaveBeenCalledTimes(1);
    expect(context.fill.mock.calls[0][1]).toBe('evenodd');
    expect(context.fill.mock.calls[0][0].d).toContain('M2,-2');
    drawing.render([800, 600], [4, 0, 0, -4, 20, 30]);
    expect(context.lineWidth).toBe(0.2);
    expect(context.stroke).toHaveBeenCalledTimes(2);
  });
  it('uses the map pixel ratio independently of the window pixel ratio', () => {
    const drawing = createOverviewCanvas(markup);
    const image = drawing.render([800, 600], [1, 0, 0, -1, 20, 30], 1.5);
    expect(image).toBe(drawing.element);
    expect(image.width).toBe(1200);
    expect(image.height).toBe(900);
    expect(context.setTransform).toHaveBeenLastCalledWith(
      1.5,
      0,
      -0,
      1.5,
      30,
      45,
    );
    expect(context.lineWidth).toBe(0.8);
    expect(drawing.element.style.transform).toBe('');
  });
  it('keeps each overlapping area separate rather than cancelling their fill', () => {
    const drawing = createOverviewCanvas(
      markup.replace(
        'M10000,10000L10010,10000L10010,10010Z',
        'M0,0L10,0L10,-10Z',
      ),
    );
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30]);
    expect(context.fill).toHaveBeenCalledTimes(2);
    expect(context.stroke).toHaveBeenCalledTimes(2);
  });
  it('supports rotation and retains relative paths without unsafe culling', () => {
    const drawing = createOverviewCanvas(
      markup.replace(
        'M10000,10000L10010,10000L10010,10010Z',
        'm0,0l10,0l0,-10z',
      ),
    );
    drawing.render([800, 600], [0, 2, 2, 0, 30, 40]);
    expect(context.setTransform).toHaveBeenLastCalledWith(0, 4, -4, -0, 60, 80);
    expect(context.lineWidth).toBe(0.4);
    expect(context.fill).toHaveBeenCalledTimes(2);
    expect(context.stroke).toHaveBeenCalledTimes(2);
  });
});
