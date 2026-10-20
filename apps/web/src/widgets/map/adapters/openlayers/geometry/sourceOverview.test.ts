import { describe, it, expect } from 'vitest';
import { readSourceOverview, visibleOverviewSvg } from './sourceOverview';

describe('native drawing overview', () => {
  it('uses source layer visibility without exposing the SVG as interactive geometry', () => {
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg"><g data-source-layer="Survey &amp; ground"><path d="M0,0L1,1"/></g><g data-source-layer="Networks"><path d="M2,2L3,3"/></g></svg>';
    const visible = visibleOverviewSvg(svg, ['Survey & ground']);
    expect(visible).not.toContain('M0,0');
    expect(visible).toContain('M2,2');
    expect(visibleOverviewSvg(svg, [])).toBe(svg);
  });
  it('ignores missing or failed overviews, retaining the vector fallback', () => {
    expect(readSourceOverview()).toBeUndefined();
    expect(
      readSourceOverview({ source_overview: { svg: null } }),
    ).toBeUndefined();
    expect(
      readSourceOverview({
        source_overview: { svg: '<svg/>', extent: [0, NaN, 1, 1] },
      }),
    ).toBeUndefined();
  });
});
