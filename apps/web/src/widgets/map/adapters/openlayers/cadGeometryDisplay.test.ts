import { describe, expect, it } from 'vitest';
import Feature from 'ol/Feature';
import type Map from 'ol/Map';
import View from 'ol/View';
import LineString from 'ol/geom/LineString';
import Polygon from 'ol/geom/Polygon';
import Snap from 'ol/interaction/Snap';
import VectorImageLayer from 'ol/layer/VectorImage';
import VectorSource from 'ol/source/Vector';
import Style from 'ol/style/Style';
import { cadGeometryDisplay } from './cadGeometryDisplay';
import { nearestLineFeature } from './rowAxis';
import { mapAreaTargetFromFeature } from './hitTargets';
import { syncGeometryVisibility } from './geometrySources';

function fixture() {
  const source = () => new VectorSource();
  const sources = {
    baseSource: source(),
    zoneSource: source(),
    constraintSource: source(),
    physicalObstacleSource: source(),
  };
  const style = new Style();
  const layers = {
    base: new VectorImageLayer({ source: sources.baseSource, style }),
    zones: new VectorImageLayer({ source: sources.zoneSource, style }),
    constraints: new VectorImageLayer({
      source: sources.constraintSource,
      style,
    }),
  };
  return { sources, layers, style };
}

describe('native CAD source visual ownership', () => {
  it('preserves source picking, snapping, attribution and obstacles while hiding raw visuals', () => {
    const { sources, layers, style } = fixture();
    const line = new Feature({
      geometry: new LineString([
        [0, 0],
        [100, 0],
      ]),
      kind: 'road',
      source_layer: 'Roads',
      source_id: 'line-1',
    });
    const area = new Feature({
      geometry: new Polygon([
        [
          [0, 10],
          [10, 10],
          [10, 20],
          [0, 20],
          [0, 10],
        ],
      ]),
      kind: 'building',
      source_layer: 'Buildings',
      source_id: 'building-1',
    });
    const utility = new Feature({
      geometry: line.getGeometry()!.clone(),
      kind: 'utility',
      source_layer: 'Pipes',
    });
    const forbidden = new Feature({
      geometry: area.getGeometry()!.clone(),
      kind: 'forbidden',
      rule_id: 'rule-1',
    });
    const zone = new Feature({
      geometry: area.getGeometry()!.clone(),
      kind: 'allowed',
    });
    const features = [line, area, utility, forbidden, zone];
    area.setId('building-1');
    const originalTarget = mapAreaTargetFromFeature(area, [5, 15]);
    syncGeometryVisibility(features, sources, new Set(), new Set());
    const snap = new Snap({ source: sources.baseSource, pixelTolerance: 12 });
    const view = new View({ center: [0, 0], resolution: 1 });
    const map = {
      getView: () => view,
      getPixelFromCoordinate: (coordinate: number[]) => coordinate,
    } as unknown as Map;
    snap.setMap(map);
    const display = cadGeometryDisplay(layers);
    display.ready('source');
    expect(layers.base.getVisible()).toBe(false);
    expect(layers.zones.getVisible()).toBe(true);
    expect(layers.constraints.getVisible()).toBe(true);
    expect(layers.constraints.getStyleFunction()!(utility, 1)).toBeUndefined();
    expect(layers.constraints.getStyleFunction()!(forbidden, 1)).toEqual([
      style,
    ]);
    expect(nearestLineFeature([sources.baseSource], [50, 1], 2)).toBe(line);
    expect(sources.baseSource.getFeaturesAtCoordinate([5, 15])).toContain(area);
    expect(mapAreaTargetFromFeature(area, [5, 15])).toEqual(originalTarget);
    expect(originalTarget.sourceId).toContain('building-1');
    expect(snap.snapTo([50, 1], [50, 1], map)?.feature).toBe(line);
    expect(sources.physicalObstacleSource.hasFeature(area)).toBe(true);
    syncGeometryVisibility(features, sources, new Set(['Roads']), new Set());
    expect(
      nearestLineFeature([sources.baseSource], [50, 1], 2),
    ).toBeUndefined();
    expect(snap.snapTo([50, 1], [50, 1], map)).toBeNull();
    display.restore();
    // OL accepts null on detach; its generated Snap declaration narrows Map.
    snap.setMap(null as unknown as Map);
  });

  it('keeps dynamic render-mode style changes and restores captured visibility', () => {
    const { layers } = fixture();
    const design = new Style();
    const cad = new Style();
    let mode: 'design' | 'cad' = 'design';
    const renderStyle = () => (mode === 'design' ? design : cad);
    layers.constraints.setStyle(renderStyle);
    layers.zones.setVisible(false);
    const display = cadGeometryDisplay(layers);
    const forbidden = new Feature({ kind: 'forbidden' });
    display.ready('source');
    expect(layers.constraints.getStyleFunction()!(forbidden, 1)).toBe(design);
    mode = 'cad';
    layers.constraints.changed();
    expect(layers.constraints.getStyleFunction()!(forbidden, 1)).toBe(cad);
    expect(layers.zones.getVisible()).toBe(false);
    display.restore();
    expect(layers.constraints.getStyle()).toBe(renderStyle);
    expect(layers.base.getVisible()).toBe(true);
    expect(layers.zones.getVisible()).toBe(false);
  });

  it('retains the existing all-geometry replacement for CAD preview', () => {
    const { layers } = fixture();
    const display = cadGeometryDisplay(layers);
    display.ready('all');
    expect(Object.values(layers).map((layer) => layer.getVisible())).toEqual([
      false,
      false,
      false,
    ]);
    display.restore();
    expect(Object.values(layers).map((layer) => layer.getVisible())).toEqual([
      true,
      true,
      true,
    ]);
  });
});
