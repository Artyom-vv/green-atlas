import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createOverviewCanvas } from './sourceOverviewCanvas';

const context = {
  setTransform: vi.fn(), fill: vi.fn(), stroke: vi.fn(),
  lineWidth: 0, lineJoin: '', fillStyle: '', strokeStyle: '', globalAlpha: 1,
};
const markup = '<svg><g data-kind="lawn" data-shape="area" fill="#123456" stroke="#123456"><path d="M0,0L10,0L10,-10Z M2,-2L3,-2L3,-3Z"/><path d="M10000,10000L10010,10000L10010,10010Z"/></g></svg>';

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('Path2D', class { constructor(public d: string) {} });
  vi.stubGlobal('devicePixelRatio', 2);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('local vector canvas overview', () => {
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
    drawing.render([800, 600], [4, 0, 0, -4, 20, 30], true);
    expect(context.lineWidth).toBe(0.2);
    expect(context.stroke).toHaveBeenCalledTimes(2);
  });
  it('reuses a panning frame but redraws when leaving it or changing zoom', () => {
    const drawing = createOverviewCanvas(markup);
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30], true);
    const count = context.stroke.mock.calls.length;
    drawing.render([800, 600], [1, 0, 0, -1, 120, 80], true);
    expect(context.stroke).toHaveBeenCalledTimes(count);
    expect(drawing.element.style.transform).toBe('translate3d(100px, 50px, 0)');
    drawing.render([800, 600], [1, 0, 0, -1, 120, 80]);
    expect(context.stroke).toHaveBeenCalledTimes(count + 1);
    expect(drawing.element.style.transform).toBe('');
  });
  it('keeps each overlapping area separate rather than cancelling their fill', () => {
    const drawing = createOverviewCanvas(markup.replace('M10000,10000L10010,10000L10010,10010Z', 'M0,0L10,0L10,-10Z'));
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30]);
    expect(context.fill).toHaveBeenCalledTimes(2);
    expect(context.stroke).toHaveBeenCalledTimes(2);
  });
  it('supports rotation and retains relative paths without unsafe culling', () => {
    const drawing = createOverviewCanvas(markup.replace('M10000,10000L10010,10000L10010,10010Z', 'm0,0l10,0l0,-10z'));
    drawing.render([800, 600], [0, 2, 2, 0, 30, 40]);
    expect(context.setTransform).toHaveBeenLastCalledWith(0, 4, -4, -0, 60, 80);
    expect(context.lineWidth).toBe(0.4);
    expect(context.fill).toHaveBeenCalledTimes(2);
    expect(context.stroke).toHaveBeenCalledTimes(2);
  });
});
