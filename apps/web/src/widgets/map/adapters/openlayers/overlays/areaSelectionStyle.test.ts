import { expect, it } from 'vitest';
import Feature from 'ol/Feature';
import Polygon from 'ol/geom/Polygon';
import { drawAreaStyles, plantingZoneFocusStyle } from './mapStyles';

it('keeps original boundaries and holes while separating finished scope from drawing', () => {
  const polygon = new Polygon([
    [
      [0, 0],
      [10, 0],
      [10, 10],
      [0, 10],
      [0, 0],
    ],
    [
      [2, 2],
      [2, 4],
      [4, 4],
      [4, 2],
      [2, 2],
    ],
  ]);
  const feature = new Feature(polygon);
  for (const style of [...plantingZoneFocusStyle, ...drawAreaStyles]) {
    expect(style.getGeometryFunction()(feature)).toBe(polygon);
  }
  expect(polygon.intersectsCoordinate([3, 3])).toBe(false);
  expect(polygon.intersectsCoordinate([7, 7])).toBe(true);
  expect(plantingZoneFocusStyle[1].getStroke()?.getLineDash()).toBeNull();
  expect(drawAreaStyles[1].getStroke()?.getLineDash()).not.toBeNull();
});
