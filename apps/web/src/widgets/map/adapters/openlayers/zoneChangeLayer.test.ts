import { zoneChangeFrame } from '@/entities/planting-zone/model/zoneChangeSummary';
import {
  zoneChangeFeatures,
  zoneChangeStyle,
} from '@/widgets/map/adapters/openlayers/zoneChangeLayer';
import { describe, expect, it } from 'vitest';
import { zonePreviewFixture } from '@/test/zoneChangeFixture';

describe('zone proposal map overlay', () => {
  it('draws only the saved target before and after, preserving distinct style and fit extents', () => {
    const preview = zonePreviewFixture('contour');
    const features = zoneChangeFeatures(preview);
    expect(features.map((feature) => feature.get('zonePreviewRole'))).toEqual([
      'before',
      'after',
    ]);
    expect(
      features.map((feature) => feature.getGeometry()!.getExtent()),
    ).toEqual([
      [0, 0, 10, 10],
      [0, 0, 12, 10],
    ]);
    expect(zoneChangeStyle(features[0]).getStroke()?.getLineDash()).toEqual([
      7, 5,
    ]);
    expect(zoneChangeStyle(features[1]).getStroke()?.getColor()).toBe(
      '#225cff',
    );
    expect(zoneChangeFrame(preview)).toEqual({
      type: 'GeometryCollection',
      geometries: [
        preview.before_zones[0].geometry,
        preview.after_zones[0].geometry,
      ],
    });
    expect(preview.before_zones).toHaveLength(2);
  });
  it.each([
    ['create', 'after'],
    ['rename', 'after'],
    ['delete', 'deletion'],
  ] as const)('draws %s with one correct contour', (operation, role) => {
    const features = zoneChangeFeatures(zonePreviewFixture(operation));
    expect(features.map((feature) => feature.get('zonePreviewRole'))).toEqual([
      role,
    ]);
    expect(features[0].get('planting_zone_id')).toBeUndefined();
    if (operation === 'delete')
      expect(zoneChangeStyle(features[0]).getStroke()?.getColor()).toBe(
        '#b42318',
      );
  });
  it('does not invent a target when the saved response is inconsistent', () => {
    const preview = zonePreviewFixture();
    preview.target_zone_id = 'missing';
    expect(zoneChangeFeatures(preview)).toEqual([]);
    expect(zoneChangeFrame(preview)).toBeUndefined();
  });
});
