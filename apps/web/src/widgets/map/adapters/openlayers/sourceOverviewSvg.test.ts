import { describe, expect, it } from 'vitest';
import { createOverviewSvg } from './sourceOverviewSvg';

describe('resolution-independent overview', () => {
  it('uses composited translation while panning, then restores exact vector placement', () => {
    const drawing = createOverviewSvg('<svg><g stroke="#123456"><path d="M0,0L10,-10"/></g></svg>');
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30], true);
    const original = drawing.element.firstElementChild?.getAttribute('transform');
    drawing.render([800, 600], [1, 0, 0, -1, 120, 80], true);
    expect(drawing.element.firstElementChild?.getAttribute('transform')).toBe(original);
    expect(drawing.element.style.transform).toBe('translate3d(100px, 50px, 0)');
    drawing.render([800, 600], [1, 0, 0, -1, 120, 80]);
    expect(drawing.element.firstElementChild?.getAttribute('transform')).toBe('matrix(1 0 0 1 120 80)');
    expect(drawing.element.style.transform).toBe('');
    expect(drawing.element.style.willChange).toBe('');
    expect(drawing.element.getAttribute('viewBox')).toBe('0 0 800 600');
  });
  it('rebases for large pans and zooms rather than revealing an empty edge', () => {
    const drawing = createOverviewSvg('<svg><g stroke="#123456"><path d="M0,0L10,-10"/></g></svg>');
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30], true);
    drawing.render([800, 600], [1, 0, 0, -1, 1000, 30], true);
    expect(drawing.element.style.transform).toBe('');
    expect(drawing.element.firstElementChild?.getAttribute('transform')).toBe('matrix(1 0 0 1 1400 430)');
    drawing.render([800, 600], [2, 0, 0, -2, 1000, 30], true);
    expect(drawing.element.firstElementChild?.getAttribute('transform')).toBe('matrix(2 0 0 2 1400 430)');
  });
  it('redraws paths at screen resolution through zoom, pan and rotation', () => {
    const drawing = createOverviewSvg('<svg><g stroke="#123456"><path d="M0,0L10,-10"/></g></svg>');
    drawing.render([800, 600], [1, 0, 0, -1, 20, 30]);
    expect(drawing.element.firstElementChild?.getAttribute('transform'))
      .toBe('matrix(1 0 0 1 20 30)');
    drawing.render([1200, 800], [0, 16, 16, 0, 40, 50]);
    expect(drawing.element.firstElementChild?.getAttribute('transform'))
      .toBe('matrix(0 16 -16 0 40 50)');
    expect(drawing.element.getAttribute('viewBox')).toBe('0 0 1200 800');
    expect(drawing.element.querySelector('path')?.getAttribute('vector-effect'))
      .toBe('non-scaling-stroke');
    expect(drawing.element.querySelector('image')).toBeNull();
  });
  it('rebuilds only generated geometry, discarding executable markup and URLs', () => {
    const drawing = createOverviewSvg('<svg><script>alert(1)</script><g stroke="url(https://example.com)"><path d="M0,0L1,1" onclick="alert(1)"/><foreignObject/></g></svg>');
    expect(drawing.element.outerHTML).not.toContain('alert');
    expect(drawing.element.outerHTML).not.toContain('https:');
    expect(drawing.element.querySelector('foreignObject')).toBeNull();
    expect(drawing.element.querySelector('path')).not.toBeNull();
  });
});
