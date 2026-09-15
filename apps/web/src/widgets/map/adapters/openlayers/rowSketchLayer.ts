import {
  rowSketch,
  type RowAxis,
  type RowSketchSettings,
} from '@/entities/planting/model/rowSketch';
import type { PlantingZoneAssignment } from '@green/api-client';
import Feature from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import LineString from 'ol/geom/LineString';
import Point from 'ol/geom/Point';
import { Circle, Fill, Stroke, Style, Text } from 'ol/style';

const axisStyle = new Style({
  stroke: new Stroke({ color: '#225cff', width: 3 }),
});
const siteStyle = (blocked: boolean) =>
  new Style({
    image: new Circle({
      radius: 5,
      fill: new Fill({ color: '#fff' }),
      stroke: new Stroke({
        color: blocked ? '#b12a20' : '#225cff',
        width: 2,
        lineDash: blocked ? undefined : [3, 2],
      }),
    }),
  });
const styles = { inside: siteStyle(false), outside: siteStyle(true) };
export function rowSketchFeatures(
  axis: RowAxis,
  settings: RowSketchSettings,
  zones: PlantingZoneAssignment[],
  showSites: boolean,
) {
  const sketch = rowSketch(axis, settings);
  if (!sketch.length) return { features: [], count: 0, outside: 0 };
  const line = new Feature(new LineString(axis.coordinates));
  line.setStyle(axisStyle);
  const features: Feature[] = [line];
  for (const [coordinate, label] of [
    [axis.coordinates[0], 'Начало'],
    [axis.coordinates.at(-1)!, 'Конец'],
  ] as const) {
    const point = new Feature(new Point(coordinate));
    point.setStyle(
      new Style({
        image: new Circle({
          radius: 5,
          fill: new Fill({ color: '#225cff' }),
          stroke: new Stroke({ color: '#fff', width: 2 }),
        }),
        text: new Text({
          text: label,
          font: '500 12px Onest, sans-serif',
          offsetY: -16,
          fill: new Fill({ color: '#163a77' }),
          stroke: new Stroke({ color: '#fff', width: 4 }),
        }),
      }),
    );
    features.push(point);
  }
  const polygons = zones.map((zone) =>
    new GeoJSON().readGeometry(zone.geometry),
  );
  let outside = 0;
  if (showSites)
    for (const site of sketch.sites) {
      const inside = polygons.some((polygon) =>
        polygon.intersectsCoordinate([site.x, site.y]),
      );
      if (!inside) outside++;
      const feature = new Feature(new Point([site.x, site.y]));
      feature.setStyle(inside ? styles.inside : styles.outside);
      features.push(feature);
    }
  return { features, count: showSites ? sketch.sites.length : 0, outside };
}
