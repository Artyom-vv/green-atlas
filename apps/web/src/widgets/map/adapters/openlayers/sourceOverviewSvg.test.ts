import { describe, expect, it } from 'vitest';
import { createOverviewSvg } from './sourceOverviewSvg';

describe('resolution-independent overview', () => {
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
