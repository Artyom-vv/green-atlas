import type { PlanObject } from '@green/api-client';
import { describe, expect, it } from 'vitest';
import { growthOverlayForecasts } from '@/entities/planting-forecast/model/growthOverlayForecasts';
import { growthEnvelopeFeatures } from './overlays/planFeatures';
import { growthEnvelopeStyle, planStyle } from './overlays/mapStyles';
import Feature from 'ol/Feature';
import Circle from 'ol/geom/Circle';
import Point from 'ol/geom/Point';
import { Circle as CircleStyle, Icon } from 'ol/style';

const object = {
  id: 'tree-1',
  kind: 'tree',
  x: 20,
  y: 20,
  radius: 1.6,
  size_class: 'standard',
  spacing_policy: 'balanced',
  locked: false,
  status: 'valid',
  canopy_forecast: [
    {
      horizon_year: 20,
      radius_min_m: 3.3,
      radius_max_m: 7,
      confidence: 'low',
      basis: 'test',
    },
    {
      horizon_year: 30,
      radius_min_m: 3.6,
      radius_max_m: 7.2,
      confidence: 'low',
      basis: 'test',
    },
  ],
  root_forecast: [],
} as PlanObject;

describe('growth overlay forecasts', () => {
  it('shows the whole plan at a future horizon when nothing is selected', () => {
    expect(growthOverlayForecasts([object], [], 20)).toHaveLength(1);
    expect(growthOverlayForecasts([object], [], 0)).toEqual([]);
  });
  it('uses the same interpolated forecast as the inspector at year 23', () => {
    const overlays = growthOverlayForecasts([object], ['tree-1'], 23);

    expect(overlays).toHaveLength(1);
    expect(overlays[0].canopy?.horizon_year).toBe(23);
    expect(overlays[0].canopy?.radius_min_m).toBeCloseTo(3.39);
    expect(overlays[0].canopy?.radius_max_m).toBeCloseTo(7.06);
    expect(overlays[0].selected).toBe(true);
  });

  it('does not render a forecast overlay outside the available anchors', () => {
    expect(growthOverlayForecasts([object], ['tree-1'], 10)).toEqual([]);
  });

  it('shows the initial forecast at zero when the catalogue has that anchor', () => {
    const initial = {
      ...object,
      canopy_forecast: [
        { ...object.canopy_forecast![0], horizon_year: 0, radius_max_m: 1 },
        ...object.canopy_forecast!,
      ],
    } as PlanObject;
    expect(growthOverlayForecasts([initial], [], 0)).toHaveLength(1);
    expect(growthOverlayForecasts([initial], [], undefined)).toEqual([]);
  });

  it('uses a preview update once instead of duplicating the durable object', () => {
    const preview = {
      ...object,
      canopy_forecast:
        object.canopy_forecast?.map((item) => ({
          ...item,
          radius_min_m: item.radius_min_m + 1,
        })) ?? [],
    } as PlanObject;

    const overlays = growthOverlayForecasts([object], ['tree-1'], 23, [
      preview,
    ]);

    expect(overlays).toHaveLength(1);
    expect(overlays[0].object).toBe(preview);
    expect(overlays[0].canopy?.radius_min_m).toBeCloseTo(4.39);
  });
});

describe('growth envelope display', () => {
  const forecasts = (selected: boolean) => [
    {
      object,
      selected,
      canopy: object.canopy_forecast![0],
      roots: {
        ...object.canopy_forecast![0],
        radius_min_m: 5,
        radius_max_m: 9,
      },
    },
  ];

  it('renders an upper-bound crown and lower-bound roots only for the selected planting', () => {
    const unselected = growthEnvelopeFeatures(forecasts(false));
    expect(unselected).toHaveLength(1);
    expect(unselected[0].get('envelopeStyle')).toBe('canopy');
    expect((unselected[0].getGeometry() as Circle).getRadius()).toBe(7);

    const selected = growthEnvelopeFeatures(forecasts(true));
    expect(selected).toHaveLength(2);
    expect(selected.map((feature) => feature.get('envelopeStyle'))).toEqual([
      'canopy',
      'roots',
    ]);
    expect((selected[1].getGeometry() as Circle).getRadius()).toBe(5);
    expect((selected[1].getGeometry() as Circle).getCenter()).toEqual([20, 20]);
    expect(
      selected.every(
        (feature) =>
          !growthEnvelopeStyle(feature).getStroke()?.getLineDash()?.length,
      ),
    ).toBe(true);
    expect(growthEnvelopeStyle(selected[0]).getFill()).not.toBeNull();
    expect(growthEnvelopeStyle(selected[1]).getFill()).toBeNull();
    expect(object.radius).toBe(1.6);
    expect(object.canopy_forecast![0].radius_min_m).toBe(3.3);
  });
  it('не рисует корневые кольца для массового выделения', () => {
    const first = forecasts(true)[0];
    const features = growthEnvelopeFeatures([
      first,
      { ...first, object: { ...first.object, id: 'another' } },
    ]);
    expect(features).toHaveLength(2);
    expect(
      features.every((feature) => feature.get('envelopeStyle') === 'canopy'),
    ).toBe(true);
  });

  it('replaces the selected mounting contour while retaining its selection and error marker', () => {
    const feature = new Feature({
      geometry: new Circle([20, 20], 1.6),
      objectId: object.id,
      kind: 'tree',
      status: 'error',
      _growthEnvelopeShown: true,
    });
    const result = planStyle(feature, new Set([object.id!]), 0.25);
    const styles = Array.isArray(result) ? result : [result];
    expect(
      styles.every(
        (style) => style.getGeometryFunction()(feature) instanceof Point,
      ),
    ).toBe(true);
    expect(
      styles.some((style) => {
        const image = style.getImage();
        return (
          image instanceof Icon &&
          decodeURIComponent(image.getSrc() ?? '').includes('#D92D20')
        );
      }),
    ).toBe(true);
    expect(
      styles.some((style) => {
        const image = style.getImage();
        return (
          image instanceof CircleStyle &&
          image.getStroke()?.getColor() === '#225CFF'
        );
      }),
    ).toBe(true);
    expect((feature.getGeometry() as Circle).getRadius()).toBeCloseTo(1.6);

    feature.set('_growthEnvelopeShown', false);
    const restored = planStyle(feature, new Set([object.id!]), 0.25);
    expect(
      (Array.isArray(restored) ? restored : [restored]).some(
        (style) => style.getGeometryFunction()(feature) instanceof Circle,
      ),
    ).toBe(true);
  });
});
