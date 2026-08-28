import { describe, expect, it, vi } from 'vitest';
import { assignmentFromGeometry } from './plantingZones';

describe('planting zone assignments', () => {
  it('turns a selected DXF polygon into a separate working area without a hidden programme', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1234);
    const geometry = { type: 'Polygon' as const, coordinates: [[[0, 0], [4, 0], [4, 4], [0, 0]]] };
    expect(assignmentFromGeometry(geometry, 2, 'Контур DXF: газон')).toEqual({
      id: 'manual-zone-1234-2',
      label: 'Контур DXF: газон',
      geometry,
    });
    vi.restoreAllMocks();
  });

  it('keeps a stable identity for a selected source contour', () => {
    const geometry = { type: 'Polygon' as const, coordinates: [[[0, 0], [4, 0], [4, 4], [0, 0]]] };

    expect(assignmentFromGeometry(geometry, 1, 'Контур DXF: газон', 'source-zone-dxf-42')).toMatchObject({
      id: 'source-zone-dxf-42',
      label: 'Контур DXF: газон',
    });
  });
});
