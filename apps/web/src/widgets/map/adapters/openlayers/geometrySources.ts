import type Feature from 'ol/Feature';
import Polygon from 'ol/geom/Polygon';
import MultiPolygon from 'ol/geom/MultiPolygon';
import type VectorSource from 'ol/source/Vector';
import {
  isTechnical3dMapFeature,
  plantingZoneIsShadowedByDraft,
} from './hitTargets';

export interface GeometrySources {
  baseSource: VectorSource;
  zoneSource: VectorSource;
  constraintSource: VectorSource;
  physicalObstacleSource: VectorSource;
}

function visibleSource(
  feature: Feature,
  sources: GeometrySources,
  hidden: ReadonlySet<string>,
  draftIds: ReadonlySet<string>,
): VectorSource | undefined {
  if (
    isTechnical3dMapFeature(feature) ||
    hidden.has(String(feature.get('source_layer'))) ||
    plantingZoneIsShadowedByDraft(feature, draftIds)
  )
    return;
  const kind = String(feature.get('kind'));
  if (kind === 'allowed' || kind === 'planting_area') return sources.zoneSource;
  if (['forbidden', 'utility', 'water', 'restricted'].includes(kind))
    return sources.constraintSource;
  return sources.baseSource;
}

/** Batch new entries; retain unchanged source membership and spatial indexes. */
export class GeometrySourceChanges {
  private readonly additions = new Map<VectorSource, Set<Feature>>();
  private renderChanged = false;

  constructor(private readonly sources: GeometrySources) {}

  private membership(source: VectorSource, feature: Feature, include: boolean) {
    let pending = this.additions.get(source);
    if (!pending) {
      pending = new Set();
      this.additions.set(source, pending);
    }
    const present = source.hasFeature(feature);
    if (
      include
        ? present || pending.has(feature)
        : !present && !pending.has(feature)
    )
      return;
    if (source !== this.sources.physicalObstacleSource)
      this.renderChanged = true;
    if (include) pending.add(feature);
    else {
      pending.delete(feature);
      if (present) source.removeFeature(feature);
    }
  }

  sync(
    feature: Feature,
    hidden: ReadonlySet<string>,
    draftIds: ReadonlySet<string>,
  ) {
    const visible = visibleSource(feature, this.sources, hidden, draftIds);
    for (const source of [
      this.sources.baseSource,
      this.sources.zoneSource,
      this.sources.constraintSource,
    ])
      this.membership(source, feature, source === visible);
    const shape = feature.getGeometry();
    const obstacle =
      ['building', 'water', 'restricted'].includes(
        String(feature.get('kind')),
      ) &&
      (shape instanceof Polygon || shape instanceof MultiPolygon) &&
      !isTechnical3dMapFeature(feature);
    this.membership(this.sources.physicalObstacleSource, feature, obstacle);
  }

  remove(feature: Feature) {
    for (const source of [
      this.sources.baseSource,
      this.sources.zoneSource,
      this.sources.constraintSource,
      this.sources.physicalObstacleSource,
    ])
      this.membership(source, feature, false);
  }

  flush() {
    for (const [source, additions] of this.additions)
      if (additions.size) source.addFeatures([...additions]);
    this.additions.clear();
    const changed = this.renderChanged;
    this.renderChanged = false;
    return changed;
  }
}

export function syncGeometryVisibility(
  features: Iterable<Feature>,
  sources: GeometrySources,
  hidden: ReadonlySet<string>,
  draftIds: ReadonlySet<string>,
) {
  const changes = new GeometrySourceChanges(sources);
  for (const feature of features) changes.sync(feature, hidden, draftIds);
  return changes.flush();
}
