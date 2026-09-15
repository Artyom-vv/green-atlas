import { MAP_DESIGN_PALETTE, MAP_DESIGN_TEXT_COLOR } from '../../model/mapDesignPalette';
import {
  isPrimarySingleBlockComponent,
  sourceBlockCaption,
} from '@/entities/source-data/model/sourceLabels';
import { BoundedLruCache } from '@/shared/cache/BoundedLruCache';
import type { FeatureLike } from 'ol/Feature';
import Circle from 'ol/geom/Circle';
import Point from 'ol/geom/Point';
import {
  Circle as CircleStyle,
  Fill,
  Icon,
  Stroke,
  Style,
  Text as TextStyle,
} from 'ol/style';

export const MAX_CACHED_GEOMETRY_STYLES = 512;

export const MAX_CACHED_BLOCK_LABEL_STYLES = 320;

export const colors: Record<string, string> = {
  site_border: '#163A5F',
  building: '#A7B0BC',
  road: '#7A8795',
  utility: '#4E78B8',
  existing_green: '#2E9C67',
  water: '#4E91B8',
  restricted: '#9A6700',
  allowed: '#91CFAE',
  forbidden: '#C76B00',
};

export const drawStyle = new Style({
  stroke: new Stroke({ color: '#225CFF', width: 2, lineDash: [7, 5] }),
  fill: new Fill({ color: 'rgba(34,92,255,.08)' }),
  image: new CircleStyle({
    radius: 4,
    fill: new Fill({ color: '#ffffff' }),
    stroke: new Stroke({ color: '#225CFF', width: 2 }),
  }),
});

// Completed brush gestures remain visible until the operator clears or applies
// them. The wide translucent band communicates the affected area while the
// crisp centre line keeps overlapping strokes legible on dense DXF geometry.
export function brushStrokeStyle(
  feature: FeatureLike,
  resolution: number,
): Style[] {
  const widthM = Number(feature.get('brushWidthM') ?? 12);
  const subtract = feature.get('brushMode') === 'subtract';
  const color = subtract ? '#D92D20' : '#225CFF';
  return [
    new Style({
      stroke: new Stroke({
        color: subtract ? 'rgba(217,45,32,.18)' : 'rgba(34,92,255,.16)',
        width: Math.max(8, widthM / Math.max(resolution, 0.0001)),
        lineCap: 'round',
        lineJoin: 'round',
      }),
    }),
    new Style({
      stroke: new Stroke({
        color,
        width: 1.75,
        lineDash: [6, 5],
        lineCap: 'round',
        lineJoin: 'round',
      }),
    }),
  ];
}

export function brushCursorStyle(feature: FeatureLike): Style[] {
  const subtract = feature.get('brushMode') === 'subtract';
  const color = subtract ? '#D92D20' : '#225CFF';
  return [
    new Style({
      fill: new Fill({ color: 'rgba(255,255,255,.5)' }),
      stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 4 }),
    }),
    new Style({ stroke: new Stroke({ color, width: 1.75, lineDash: [6, 4] }) }),
  ];
}

export const snapGuideStyle = new Style({
  image: new CircleStyle({
    radius: 6,
    fill: new Fill({ color: 'rgba(255,255,255,.94)' }),
    stroke: new Stroke({ color: '#225CFF', width: 2 }),
  }),
});

export const selectionAreaDraftStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.06)' }),
  stroke: new Stroke({ color: '#225CFF', width: 1.5, lineDash: [6, 4] }),
});

export const moveDraftStyles = {
  path: [
    new Style({
      stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 5 }),
    }),
    new Style({
      stroke: new Stroke({ color: '#225CFF', width: 1.5, lineDash: [5, 5] }),
    }),
  ],
  origin: new Style({
    image: new CircleStyle({
      radius: 7,
      fill: new Fill({ color: 'rgba(255,255,255,.82)' }),
      stroke: new Stroke({ color: '#66788A', width: 1.5, lineDash: [3, 3] }),
    }),
  }),
};

export function selectionDraftStyle(feature: FeatureLike): Style | Style[] {
  const role = feature.get('draftRole');
  if (role === 'move-path') return moveDraftStyles.path;
  if (role === 'move-origin') return moveDraftStyles.origin;
  return selectionAreaDraftStyle;
}

export function plantGlyph(
  kind: string,
  color: string,
  outline: string,
): string {
  const body =
    kind === 'shrub'
      ? '<circle cx="8" cy="11" r="4"/><circle cx="12" cy="8" r="5"/><circle cx="16" cy="11" r="4"/>'
      : '<path d="M12 2.2c-2.7 0-4.6 1.7-4.9 4.1A5 5 0 0 0 4.5 15a5.3 5.3 0 0 0 7.5 1.2A5.3 5.3 0 0 0 19.5 15a5 5 0 0 0-2.6-8.7C16.6 3.9 14.7 2.2 12 2.2Z"/><path d="M11 15h2v6h-2z"/>';
  return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="${color}" stroke="${outline}" stroke-width="1.5" stroke-linejoin="round">${body}</g></svg>`)}`;
}

export function plantMarkerMetrics(kind: string) {
  const tree = kind === 'tree';
  return {
    overviewRadius: tree ? 4.5 : 3.5,
    overviewBackdropRadius: tree ? 5.5 : 4.5,
    overviewSelectionRadius: tree ? 8 : 7,
    detailHaloRadius: tree ? 7 : 6,
    detailSelectionRadius: tree ? 10 : 9,
    glyphSize: tree ? 14 : 12,
  };
}

export const changePreviewStyles = new globalThis.Map<string, Style[]>();

export function changePreviewStyle(
  feature: FeatureLike,
  resolution = 1,
): Style[] {
  const kind = String(feature.get('kind') ?? 'tree');
  const status = String(feature.get('candidateStatus') ?? 'allowed');
  const role = String(feature.get('previewRole') ?? 'candidate');
  const semanticColor =
    status === 'blocked'
      ? '#D92D20'
      : status === 'unknown' || status === 'soft_conflict'
        ? '#B76400'
        : '#168A5B';
  const overview = resolution > 0.45;
  const marker = plantMarkerMetrics(kind);
  const key = `${kind}:${status}:${role}:${overview}`;
  const cached = changePreviewStyles.get(key);
  if (cached) return cached;
  if (role === 'delete') {
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#C83B32" stroke="white" stroke-width="2"/><path d="m8 8 8 8m0-8-8 8" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"/></svg>';
    const styles = [
      new Style({
        image: new Icon({
          src: `data:image/svg+xml,${encodeURIComponent(svg)}`,
          width: overview ? 18 : 24,
          height: overview ? 18 : 24,
        }),
      }),
    ];
    changePreviewStyles.set(key, styles);
    return styles;
  }
  if (role === 'move-path') {
    const styles = [
      new Style({
        stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 5 }),
      }),
      new Style({
        stroke: new Stroke({
          color: semanticColor,
          width: 1.75,
          lineDash: [6, 5],
        }),
      }),
    ];
    changePreviewStyles.set(key, styles);
    return styles;
  }
  if (role === 'move-origin') {
    const styles = [
      new Style({
        image: new CircleStyle({
          radius: overview ? 6 : 7,
          fill: new Fill({ color: 'rgba(255,255,255,.82)' }),
          stroke: new Stroke({
            color: '#66788A',
            width: 1.5,
            lineDash: [3, 3],
          }),
        }),
      }),
    ];
    changePreviewStyles.set(key, styles);
    return styles;
  }
  const geometry = (candidate: FeatureLike) => {
    const candidateGeometry = candidate.getGeometry();
    return candidateGeometry instanceof Circle
      ? new Point(candidateGeometry.getCenter())
      : candidate.get('markerGeometry');
  };
  const styles = overview
    ? [
        new Style({
          geometry,
          image: new CircleStyle({
            radius: marker.overviewBackdropRadius,
            fill: new Fill({ color: '#FFFFFF' }),
            stroke: new Stroke({ color: '#FFFFFF', width: 3 }),
          }),
        }),
        new Style({
          geometry,
          image: new CircleStyle({
            radius: marker.overviewRadius,
            fill: new Fill({ color: semanticColor }),
            stroke: new Stroke({ color: semanticColor, width: 1 }),
          }),
        }),
      ]
    : [
        new Style({
          geometry,
          image: new CircleStyle({
            radius: marker.detailHaloRadius,
            fill: new Fill({ color: '#FFFFFF' }),
            stroke: new Stroke({ color: semanticColor, width: 1.5 }),
          }),
        }),
        new Style({
          geometry,
          image: new Icon({
            src: plantGlyph(kind, semanticColor, '#FFFFFF'),
            width: marker.glyphSize,
            height: marker.glyphSize,
          }),
        }),
      ];
  changePreviewStyles.set(key, styles);
  return styles;
}

export const growthEnvelopeStyles = {
  canopyMax: new Style({
    fill: new Fill({ color: 'rgba(25,135,84,.10)' }),
    stroke: new Stroke({ color: 'rgba(25,135,84,.72)', width: 1.5 }),
  }),
  canopyMin: new Style({
    stroke: new Stroke({
      color: 'rgba(25,135,84,.9)',
      width: 1,
      lineDash: [3, 3],
    }),
  }),
  rootMax: new Style({
    stroke: new Stroke({
      color: 'rgba(183,100,0,.76)',
      width: 1.5,
      lineDash: [7, 4],
    }),
  }),
  rootMin: new Style({
    stroke: new Stroke({
      color: 'rgba(183,100,0,.48)',
      width: 1,
      lineDash: [2, 4],
    }),
  }),
};

export function growthEnvelopeStyle(feature: FeatureLike) {
  return growthEnvelopeStyles[
    feature.get('envelopeStyle') as keyof typeof growthEnvelopeStyles
  ];
}

export const placementPreviewStyles = new globalThis.Map<string, Style>();

export function placementPreviewStyle(feature: FeatureLike) {
  const status = String(feature.get('placementStatus') ?? 'unknown');
  const cached = placementPreviewStyles.get(status);
  if (cached) return cached;
  const colors =
    status === 'allowed'
      ? { fill: 'rgba(22,138,91,.16)', stroke: '#168A5B' }
      : status === 'blocked'
        ? { fill: 'rgba(217,45,32,.14)', stroke: '#D92D20' }
        : { fill: 'rgba(98,125,152,.12)', stroke: '#627D98' };
  const style = new Style({
    fill: new Fill({ color: colors.fill }),
    stroke: new Stroke({
      color: colors.stroke,
      width: 1.75,
      lineDash: status === 'unknown' ? [5, 4] : undefined,
    }),
    image: new CircleStyle({
      radius: 4,
      fill: new Fill({ color: colors.stroke }),
      stroke: new Stroke({ color: '#FFFFFF', width: 1.5 }),
    }),
  });
  placementPreviewStyles.set(status, style);
  return style;
}

export const mapHoverStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.06)' }),
  stroke: new Stroke({ color: '#225CFF', width: 2 }),
});

export const constraintHoverStyle = new Style({
  fill: new Fill({ color: 'rgba(199,107,0,.08)' }),
  stroke: new Stroke({ color: '#C76B00', width: 1.75, lineDash: [6, 4] }),
});

export function contextualMapHoverStyle(feature: FeatureLike) {
  return feature.get('kind') === 'forbidden'
    ? constraintHoverStyle
    : mapHoverStyle;
}

export const plantingZoneDraftStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.01)' }),
  stroke: new Stroke({ color: '#84948c', width: 1 }),
});

export const plantingZoneFocusStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.012)' }),
  stroke: new Stroke({ color: '#225CFF', width: 1.75 }),
});

// These caches live at module scope so style instances are reused while the
// map redraws. CAD captions, colours and linetypes are source-controlled,
// though: an unlimited Map would keep styles from every project opened in a
// long session. Bound the cache just like viewport geometry.
export const geometryStyles = new BoundedLruCache<string, Style>(
  MAX_CACHED_GEOMETRY_STYLES,
);

export const blockComponentLabelStyles = new BoundedLruCache<string, Style>(
  MAX_CACHED_BLOCK_LABEL_STYLES,
);

// Text labels and block attributes have an effectively unbounded value space,
// so they cannot safely live in the shared style LRU. Associate them with the
// OpenLayers feature instead: they are released when the viewport evicts that
// feature or when the map is disposed, but are still reused on every frame.
export const featureGeometryStyles = new WeakMap<
  object,
  globalThis.Map<string, Style>
>();

export function cachedFeatureGeometryStyle(
  feature: FeatureLike,
  key: string,
  create: () => Style,
): Style {
  const target = feature as object;
  let styles = featureGeometryStyles.get(target);
  if (!styles) {
    styles = new globalThis.Map();
    featureGeometryStyles.set(target, styles);
  }
  const cached = styles.get(key);
  if (cached) return cached;
  const style = create();
  styles.set(key, style);
  return style;
}

export function cachedGeometryStyle(key: string, create: () => Style): Style {
  const cached = geometryStyles.get(key);
  if (cached) return cached;
  const style = create();
  geometryStyles.set(key, style);
  return style;
}

export function blockComponentCaptionStyle(
  feature: FeatureLike,
  resolution: number,
  sourceColor: string,
): Style | undefined {
  if (
    resolution > 0.8 ||
    !isPrimarySingleBlockComponent(
      feature.get('source_block_component'),
      feature.get('source_block_instances'),
    )
  )
    return undefined;
  const caption = sourceBlockCaption(
    feature.get('source_block'),
    feature.get('source_attributes'),
  );
  if (!caption) return undefined;
  const key = `${sourceColor}:${caption}`;
  const cached = blockComponentLabelStyles.get(key);
  if (cached) return cached;
  const style = new Style({
    text: new TextStyle({
      text: caption,
      placement: 'point',
      offsetY: -12,
      font: '500 10px IBM Plex Mono',
      fill: new Fill({ color: sourceColor }),
      stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 3 }),
    }),
  });
  blockComponentLabelStyles.set(key, style);
  return style;
}

export function sourceBlockMapCaption(feature: FeatureLike): string {
  if (Number(feature.get('source_block_instances') ?? 1) > 1) return '';
  return sourceBlockCaption(
    feature.get('source_block'),
    feature.get('source_attributes'),
  );
}

export const sourceLineDash = (linetype: string) => {
  const value = linetype.toUpperCase();
  if (value.includes('DASHDOT') || value.includes('CENTER'))
    return [10, 4, 2, 4];
  if (value.includes('DASH')) return [8, 5];
  if (value.includes('DOT')) return [2, 4];
  return undefined;
};

export function designGeometryStyle(
  feature: FeatureLike,
  resolution: number,
): Style | Style[] | undefined {
  const kind = String(feature.get('kind') ?? 'ignore');
  if (kind === 'allowed' && !feature.get('source_layer')) return undefined;
  if (
    kind === 'forbidden' &&
    feature.get('rule_id') &&
    !feature.get('source_layer')
  )
    return undefined;
  const entity = String(feature.get('entity_type') ?? '');
  if (entity === 'TEXT' || entity === 'MTEXT') {
    if (resolution > 0.7) return undefined;
    return cachedFeatureGeometryStyle(
      feature,
      `design-text:${String(feature.get('source_text'))}`,
      () =>
        new Style({
          text: new TextStyle({
            text: String(feature.get('source_text') ?? ''),
            font: '400 11px Onest',
            fill: new Fill({ color: MAP_DESIGN_TEXT_COLOR }),
            stroke: new Stroke({ color: '#f4f6f8', width: 3 }),
            rotation:
              (-Number(feature.get('source_rotation') ?? 0) * Math.PI) / 180,
          }),
        }),
    );
  }
  const [fill, stroke] = MAP_DESIGN_PALETTE[kind] ?? ['rgba(0,0,0,0)', '#cdd4dc'];
  const key = `design:${kind}:${entity}:${resolution > 2 ? 'overview' : 'detail'}`;
  const style = cachedGeometryStyle(
    key,
    () =>
      new Style({
        fill: new Fill({ color: fill }),
        stroke: new Stroke({
          color: stroke,
          width: ['planting_area', 'site_border'].includes(kind)
            ? 1.6
            : kind === 'road'
              ? 1.2
              : 0.8,
          lineDash: ['utility', 'restricted'].includes(kind)
            ? [5, 3]
            : undefined,
        }),
        image: new CircleStyle({
          radius: kind === 'existing_green' ? 2 : 2.5,
          fill: new Fill({ color: fill }),
          stroke: new Stroke({ color: stroke, width: 1 }),
        }),
      }),
  );
  return feature.get('_mapHover') ? [style, mapHoverStyle] : style;
}

export function geometryStyle(feature: FeatureLike, resolution: number) {
  const kind = String(feature.get('kind') ?? 'default');
  const sourceLayer = String(feature.get('source_layer') ?? '').toLowerCase();
  const entityType = String(feature.get('entity_type') ?? '');
  const sourceColor = String(feature.get('source_color') ?? '#7A8795');
  const sourceLinetype = String(feature.get('source_linetype') ?? 'CONTINUOUS');
  // Aggregated regulation buffers stay active in hit-testing and placement
  // validation, but rendering every buffer permanently overwhelms the DXF.
  // The hovered polygon is drawn by the dedicated contextual hover layer.
  if (kind === 'forbidden' && feature.get('rule_id') && !sourceLayer)
    return undefined;
  if ((entityType === 'TEXT' || entityType === 'MTEXT') && resolution > 2.2)
    return undefined;
  if (feature.get('geometry_fallback'))
    return cachedGeometryStyle(
      `fallback:${sourceColor}`,
      () =>
        new Style({
          image: new CircleStyle({
            radius: 4.5,
            fill: new Fill({ color: '#FFFFFF' }),
            stroke: new Stroke({ color: sourceColor, width: 1.5 }),
          }),
        }),
    );
  if (
    feature.get('source_raster_frame') ||
    feature.get('source_underlay_frame')
  ) {
    const key = `external-frame:${sourceColor}:${resolution > 2.2 ? 'overview' : 'detail'}`;
    const style = cachedGeometryStyle(
      key,
      () =>
        new Style({
          fill: new Fill({ color: 'rgba(78,120,184,.035)' }),
          stroke: new Stroke({
            color: sourceColor,
            width: resolution > 2.2 ? 1 : 1.25,
            lineDash: [8, 5],
          }),
        }),
    );
    return feature.get('_mapHover') ? [style, mapHoverStyle] : style;
  }
  if (feature.get('source_proxy_graphic')) {
    const key = `proxy-context:${sourceColor}:${resolution > 2.2 ? 'overview' : 'detail'}`;
    const style = cachedGeometryStyle(
      key,
      () =>
        new Style({
          fill: new Fill({ color: 'rgba(78,120,184,.025)' }),
          stroke: new Stroke({
            color: sourceColor,
            width: resolution > 2.2 ? 0.85 : 1.15,
            lineDash: [3, 3],
          }),
        }),
    );
    return feature.get('_mapHover') ? [style, mapHoverStyle] : style;
  }
  if (feature.get('block_rendered')) {
    const caption = sourceBlockMapCaption(feature);
    const key = `block:${sourceColor}:${sourceLinetype}:${caption}:${resolution > 2.2 ? 'overview' : 'detail'}`;
    const style = cachedFeatureGeometryStyle(
      feature,
      key,
      () =>
        new Style({
          fill: new Fill({ color: 'rgba(255,255,255,0)' }),
          stroke: new Stroke({
            color: sourceColor,
            width: resolution > 2.2 ? 1 : 1.25,
            lineDash: sourceLineDash(sourceLinetype),
          }),
          text:
            resolution <= 0.8 && caption
              ? new TextStyle({
                  text: caption,
                  offsetY: -10,
                  font: '500 10px IBM Plex Mono',
                  fill: new Fill({ color: sourceColor }),
                  stroke: new Stroke({
                    color: 'rgba(255,255,255,.94)',
                    width: 3,
                  }),
                })
              : undefined,
        }),
    );
    return feature.get('_mapHover') ? [style, mapHoverStyle] : style;
  }
  if (entityType === 'TEXT' || entityType === 'MTEXT')
    return cachedFeatureGeometryStyle(
      feature,
      `text:${sourceColor}:${String(feature.get('source_text') ?? '')}:${Number(feature.get('source_rotation') ?? 0)}`,
      () =>
        new Style({
          text: new TextStyle({
            text: String(feature.get('source_text') ?? ''),
            font: '500 11px Onest',
            rotation:
              (-Number(feature.get('source_rotation') ?? 0) * Math.PI) / 180,
            fill: new Fill({ color: sourceColor }),
            stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 3 }),
            textAlign: 'left',
            offsetX: 4,
          }),
        }),
    );
  if (entityType === 'INSERT' || entityType === 'POINT') {
    const caption =
      resolution <= 0.8 && entityType === 'INSERT'
        ? sourceBlockMapCaption(feature)
        : '';
    const key = `marker:${entityType}:${sourceColor}:${caption}`;
    return caption
      ? cachedFeatureGeometryStyle(
          feature,
          key,
          () =>
            new Style({
              image: new CircleStyle({
                radius: 4,
                fill: new Fill({ color: '#ffffff' }),
                stroke: new Stroke({ color: sourceColor, width: 1.5 }),
              }),
              text: new TextStyle({
                text: caption,
                offsetY: -10,
                font: '500 10px IBM Plex Mono',
                fill: new Fill({ color: sourceColor }),
                stroke: new Stroke({
                  color: 'rgba(255,255,255,.94)',
                  width: 3,
                }),
              }),
            }),
        )
      : cachedGeometryStyle(
          key,
          () =>
            new Style({
              image: new CircleStyle({
                radius: 4,
                fill: new Fill({ color: '#ffffff' }),
                stroke: new Stroke({ color: sourceColor, width: 1.5 }),
              }),
            }),
        );
  }
  if (kind === 'forbidden' && resolution > 2.2) return undefined;
  const blockCaption = blockComponentCaptionStyle(
    feature,
    resolution,
    sourceColor,
  );
  const key = `${kind}:${resolution > 2.2 ? 'overview' : 'detail'}:${sourceLayer.includes('heat') ? 'heat' : sourceLayer.includes('hydro') ? 'hydro' : sourceLayer.includes('road_major') ? 'major' : sourceLayer.includes('road_local') ? 'local' : 'default'}:${sourceColor}:${sourceLinetype}`;
  const cached = geometryStyles.get(key);
  if (cached) {
    const styles = blockCaption ? [cached, blockCaption] : cached;
    return feature.get('_mapHover')
      ? Array.isArray(styles)
        ? [...styles, mapHoverStyle]
        : [styles, mapHoverStyle]
      : styles;
  }
  let style: Style;
  if (kind === 'allowed')
    style =
      resolution > 2.2
        ? new Style({ fill: new Fill({ color: 'rgba(145,207,174,.05)' }) })
        : new Style({
            fill: new Fill({ color: 'rgba(145,207,174,.18)' }),
            stroke: new Stroke({ color: '#72B892', width: 1 }),
          });
  else if (kind === 'planting_area')
    style = new Style({
      fill: new Fill({ color: 'rgba(25,135,84,.10)' }),
      stroke: new Stroke({ color: '#198754', width: 2 }),
    });
  else if (kind === 'forbidden')
    style = new Style({
      fill: new Fill({ color: 'rgba(199,107,0,.06)' }),
      stroke: new Stroke({ color: colors[kind], width: 1.4, lineDash: [6, 4] }),
    });
  else if (kind === 'utility')
    style = new Style({
      stroke: new Stroke({
        color: sourceLayer.includes('heat') ? '#B76400' : colors[kind],
        width: 1.5,
        lineDash: [6, 4],
      }),
    });
  else if (sourceLayer.includes('hydro'))
    style = new Style({
      fill: new Fill({ color: 'rgba(78,145,184,.12)' }),
      stroke: new Stroke({ color: '#4E91B8', width: 1 }),
    });
  else if (kind === 'building')
    style = new Style({
      fill: new Fill({ color: 'rgba(205,212,220,.62)' }),
      stroke: new Stroke({ color: '#8F9AA7', width: 1.1 }),
    });
  else if (kind === 'road') {
    const localOverview =
      sourceLayer.includes('road_local') && resolution > 2.5;
    style = new Style({
      stroke: new Stroke({
        color: sourceLayer.includes('road_major')
          ? '#596675'
          : localOverview
            ? 'rgba(167,176,188,.58)'
            : '#A7B0BC',
        width: sourceLayer.includes('road_major')
          ? 1.6
          : localOverview
            ? 0.75
            : 1,
      }),
    });
  } else if (kind === 'existing_green')
    style = new Style({
      fill: new Fill({ color: 'rgba(25,135,84,.07)' }),
      stroke: new Stroke({ color: '#5AA77F', width: 1 }),
    });
  else if (kind === 'water')
    style = new Style({
      fill: new Fill({ color: 'rgba(78,145,184,.12)' }),
      stroke: new Stroke({ color: colors[kind], width: 1.1 }),
    });
  else if (kind === 'restricted')
    style = new Style({
      fill: new Fill({ color: 'rgba(154,103,0,.08)' }),
      stroke: new Stroke({ color: colors[kind], width: 1.1, lineDash: [5, 3] }),
    });
  else
    style = new Style({
      fill: new Fill({ color: 'rgba(255,255,255,0)' }),
      stroke: new Stroke({
        color: colors[kind] ?? sourceColor,
        width:
          kind === 'site_border'
            ? 2
            : Math.max(
                0.8,
                Number(feature.get('source_lineweight_mm') ?? 1.1) || 1.1,
              ),
        lineDash: sourceLineDash(sourceLinetype),
      }),
    });
  geometryStyles.set(key, style);
  const styles = blockCaption ? [style, blockCaption] : style;
  return feature.get('_mapHover')
    ? Array.isArray(styles)
      ? [...styles, mapHoverStyle]
      : [styles, mapHoverStyle]
    : styles;
}

export const planStyles = new globalThis.Map<string, Style | Style[]>();

export function planStyle(
  feature: FeatureLike,
  selectedIds: ReadonlySet<string> | undefined,
  resolution: number,
) {
  const kind = String(feature.get('kind') ?? 'tree');
  const status = String(feature.get('status') ?? 'valid');
  const liveStatus = feature.get('_liveCandidateStatus');
  const visualStatus = feature.get('_localIntersection')
    ? 'blocked'
    : typeof liveStatus === 'string'
      ? liveStatus
      : feature.get('_metadataOnly') && status !== 'error'
        ? 'unassigned'
        : status;
  const selected = selectedIds?.has(String(feature.get('objectId'))) ?? false;
  // A selected object remains actionable even when a zone filter is active.
  // Muting it used to erase both selection and validation status.
  const zoneMuted = Boolean(feature.get('_zoneMuted')) && !selected;
  const overview = resolution > 0.45;
  const marker = plantMarkerMetrics(kind);
  const key = `${kind}:${visualStatus}:${selected}:${overview}:${zoneMuted}`;
  const cached = planStyles.get(key);
  if (cached) return cached;
  const semanticColor =
    visualStatus === 'error' || visualStatus === 'blocked'
      ? '#D92D20'
      : visualStatus === 'warning' ||
          visualStatus === 'soft_conflict' ||
          visualStatus === 'unknown'
        ? '#B76400'
        : visualStatus === 'unassigned'
          ? '#596675'
          : visualStatus === 'checking'
            ? '#225CFF'
            : '#168A5B';
  const markerColor = zoneMuted ? '#91A0AE' : semanticColor;
  const fill = zoneMuted
    ? 'rgba(122,135,149,.08)'
    : visualStatus === 'error' || visualStatus === 'blocked'
      ? 'rgba(217,45,32,.10)'
      : visualStatus === 'warning' ||
          visualStatus === 'soft_conflict' ||
          visualStatus === 'unknown'
        ? 'rgba(183,100,0,.10)'
        : kind === 'tree'
          ? 'rgba(22,138,91,.18)'
          : 'rgba(114,184,146,.20)';
  const crownStyle = new Style({
    fill: new Fill({ color: fill }),
    stroke: new Stroke({
      color: markerColor,
      width: selected ? 2 : 1.25,
      lineDash: selected ? [7, 4] : undefined,
    }),
  });
  const hitStyle = new Style({
    fill: new Fill({ color: 'rgba(0,0,0,0.001)' }),
  });
  const markerGeometry = (candidate: FeatureLike) => {
    const geometry = candidate.getGeometry();
    return geometry instanceof Circle
      ? new Point(geometry.getCenter())
      : candidate.get('markerGeometry');
  };
  const markerStyle = new Style({
    geometry: markerGeometry,
    image: new Icon({
      src: plantGlyph(kind, markerColor, '#FFFFFF'),
      width: marker.glyphSize,
      height: marker.glyphSize,
    }),
  });
  const markerHaloStyle = new Style({
    geometry: markerGeometry,
    image: new CircleStyle({
      radius: marker.detailHaloRadius,
      fill: new Fill({ color: 'rgba(255,255,255,.96)' }),
      stroke: new Stroke({
        color: zoneMuted ? '#91A0AE' : semanticColor,
        width: 1.25,
      }),
    }),
  });
  const compactMarkerStyle = new Style({
    geometry: markerGeometry,
    image: new CircleStyle({
      radius: marker.overviewRadius,
      fill: new Fill({ color: markerColor }),
      stroke: new Stroke({ color: '#FFFFFF', width: 2 }),
    }),
  });
  const selectionMarkerStyle = new Style({
    geometry: markerGeometry,
    image: new CircleStyle({
      radius: overview
        ? marker.overviewSelectionRadius
        : marker.detailSelectionRadius,
      fill: new Fill({ color: 'rgba(255,255,255,.96)' }),
      stroke: new Stroke({ color: '#225CFF', width: overview ? 2.5 : 3 }),
    }),
  });
  const selectedCrownBackdrop = new Style({
    stroke: new Stroke({ color: 'rgba(255,255,255,.9)', width: 5 }),
  });
  const styles = overview
    ? selected
      ? [hitStyle, selectionMarkerStyle, compactMarkerStyle]
      : [hitStyle, compactMarkerStyle]
    : selected
      ? [
          selectedCrownBackdrop,
          crownStyle,
          selectionMarkerStyle,
          markerHaloStyle,
          markerStyle,
        ]
      : [hitStyle, markerHaloStyle, markerStyle];
  planStyles.set(key, styles);
  return styles;
}

