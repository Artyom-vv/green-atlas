import { describe, expect, it } from 'vitest';
import { initialWorkspaceExtent } from './initialWorkspaceExtent';

const site = {
  mapped_kind: 'site_border' as const,
  bounds: [100, 200, 300, 400] as [number, number, number, number],
  boundary_candidate: { status: 'usable' as const, basis: 'source_surface', area_m2: 40000, inset_1_5m_area_m2: 38809, component_count: 1 },
};
describe('initial workspace camera', () => {
  it('frames the mapped territory while leaving remote CAD legends accessible', () => {
    expect(initialWorkspaceExtent([-10000, -10000, 10000, 10000], [site], false))
      .toEqual([90, 190, 310, 410]);
  });
  it('includes every chosen boundary, without selecting the largest by name', () => {
    expect(initialWorkspaceExtent(undefined, [site, { ...site, bounds: [500, 200, 700, 400] }], false))
      .toEqual([70, 190, 730, 410]);
  });
  it('does not use ignored or invalid candidates as camera authority', () => {
    expect(initialWorkspaceExtent([0, 0, 800, 800], [
      { ...site, mapped_kind: 'ignore' },
      { ...site, boundary_candidate: { ...site.boundary_candidate, status: 'thin' } },
    ], false)).toEqual([0, 0, 800, 800]);
  });
  it('keeps full-source framing for preview and legacy projects', () => {
    expect(initialWorkspaceExtent([0, 0, 100, 100], [site], true)).toEqual([-30, -30, 130, 130]);
    expect(initialWorkspaceExtent([0, 0, 100, 100], [{ ...site, bounds: null }], false)).toEqual([0, 0, 100, 100]);
  });
});
