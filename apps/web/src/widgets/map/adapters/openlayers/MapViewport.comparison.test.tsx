import { act, render } from '@testing-library/react';
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import { useOpenLayersViewport } from './useOpenLayersViewport';
import type Map from 'ol/Map';
import type VectorLayer from 'ol/layer/Vector';
import type VectorSource from 'ol/source/Vector';
import BaseEvent from 'ol/events/Event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { maps } = vi.hoisted(() => ({ maps: [] as Map[] }));
vi.mock('ol/Map', async (original) => {
  const { default: OriginalMap } = await original<typeof import('ol/Map')>();
  return {
    default: class extends OriginalMap {
      constructor(...args: ConstructorParameters<typeof OriginalMap>) {
        super(...args);
        maps.push(this);
      }
    },
  };
});

function Viewport(props: MapViewportOptions) {
  const { targetRef } = useOpenLayersViewport(props, undefined);
  return <div ref={targetRef} />;
}

beforeEach(() => {
  maps.length = 0;
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
});
afterEach(() => vi.unstubAllGlobals());

const polygon = {
  type: 'Polygon',
  coordinates: [
    [
      [0, 0],
      [100, 0],
      [100, 100],
      [0, 100],
      [0, 0],
    ],
  ],
};
function options(): MapViewportOptions {
  return {
    objects: [
      {
        id: 'saved-tree',
        kind: 'tree',
        x: 10,
        y: 20,
        radius: 1,
        locked: false,
        status: 'valid',
        spacing_policy: 'balanced',
        size_class: 'standard',
      },
    ],
    geometry: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          id: 'saved-area',
          geometry: polygon,
          properties: {
            kind: 'planting_area',
            planting_zone_id: 'area',
            source_layer: 'site',
          },
        },
      ],
    },
    draftPlantingZones: [
      { id: 'area', geometry: polygon, label: 'Рабочий участок' },
    ],
    selectedIds: ['saved-tree'],
    changePreview: {
      id: 'draft',
      digest: 'draft-digest',
      base_plan_version: 1,
      source: 'manual',
      label: 'Черновик посадки',
      expires_at: '2026-10-08T00:00:00Z',
      can_apply: true,
      additions: [
        {
          id: 'draft-tree',
          kind: 'tree',
          x: 50,
          y: 60,
          radius: 1,
          locked: false,
          status: 'valid',
          spacing_policy: 'balanced',
          size_class: 'standard',
        },
      ],
      updates: [],
      deletion_ids: [],
    },
    tool: 'select',
    onSelect: vi.fn(),
    onCoordinate: vi.fn(),
    onProbeCoordinate: vi.fn(),
  };
}
function layerAt(map: Map, zIndex: number) {
  return map
    .getLayers()
    .getArray()
    .find((layer) => layer.getZIndex() === zIndex) as VectorLayer<VectorSource>;
}

describe('mounted map comparison lifecycle', () => {
  it('keeps one actual map, camera and saved feature while hiding drafts and ignoring comparison clicks', () => {
    const props = options();
    const { rerender } = render(<Viewport {...props} />);
    const map = maps[0];
    const plan = layerAt(map, 3);
    const draft = layerAt(map, 9);
    const saved = plan.getSource()?.getFeatureById('saved-tree');
    const draftFeature = draft
      .getSource()
      ?.getFeatureById('change-preview-draft-tree');
    const view = map.getView();
    act(() => {
      view.setCenter([17, 33]);
      view.setResolution(2);
    });

    rerender(<Viewport {...props} planComparison="before" />);
    expect(maps).toEqual([map]);
    expect(map.getView()).toBe(view);
    expect(view.getCenter()).toEqual([17, 33]);
    expect(view.getResolution()).toBe(2);
    expect(plan.getVisible()).toBe(false);
    expect(draft.getVisible()).toBe(false);
    act(() =>
      map.dispatchEvent(
        Object.assign(new BaseEvent('singleclick'), {
          coordinate: [10, 20],
          pixel: [10, 20],
          originalEvent: new MouseEvent('click'),
        }),
      ),
    );
    expect(props.onSelect).not.toHaveBeenCalled();
    expect(props.onCoordinate).not.toHaveBeenCalled();
    expect(props.onProbeCoordinate).not.toHaveBeenCalled();

    rerender(<Viewport {...props} planComparison="after" />);
    expect(plan.getVisible()).toBe(true);
    expect(plan.getSource()?.getFeatureById('saved-tree')).toBe(saved);
    expect(draft.getSource()?.getFeatureById('change-preview-draft-tree')).toBe(
      draftFeature,
    );
    rerender(<Viewport {...props} planComparison="editing" />);
    expect(draft.getVisible()).toBe(true);
    expect(props.selectedIds).toEqual(['saved-tree']);
    expect(view.getCenter()).toEqual([17, 33]);
    expect(view.getResolution()).toBe(2);
    expect(maps).toEqual([map]);
  });

  it('restores source area visibility in comparison when its editing contour is shadowed by a draft', () => {
    const props = options();
    const { rerender } = render(<Viewport {...props} />);
    const areas = layerAt(maps[0], 0).getSource()!;
    expect(areas.getFeatureById('saved-area')).toBeNull();
    rerender(<Viewport {...props} planComparison="before" />);
    const sourceArea = areas.getFeatureById('saved-area');
    expect(sourceArea).not.toBeNull();
    rerender(<Viewport {...props} planComparison="after" />);
    expect(areas.getFeatureById('saved-area')).toBe(sourceArea);
    rerender(<Viewport {...props} planComparison="editing" />);
    expect(areas.getFeatureById('saved-area')).toBeNull();
    expect(props.draftPlantingZones).toHaveLength(1);
  });
});
