import type Feature from 'ol/Feature';
import { BoundedLruCache } from '@/shared/cache/BoundedLruCache';
import {
  captureFeatureContent,
  featureContentMatches,
  type FeatureContent,
} from './featureContent';
import type { RawViewportFeature } from './readViewportFeature';

interface FeatureStamp {
  content: FeatureContent;
  representation: string;
  featureRevision: number;
}

/** One map's bounded OL cache; stamps disappear with the associated Feature. */
export class ViewportFeatureCache extends BoundedLruCache<string, Feature> {
  private readonly stamps = new WeakMap<Feature, FeatureStamp>();
  private scope?: string;
  private representation?: string;
  private generation = 0;
  private snapDirty = false;

  beginLoad(scope: string, representation: string) {
    const reset =
      this.scope !== scope || this.representation !== representation;
    this.scope = scope;
    this.representation = representation;
    this.generation += 1;
    if (reset) this.clear();
    return { reset, generation: this.generation };
  }

  isCurrent(generation: number) {
    return this.generation === generation;
  }

  cancelLoad(generation: number) {
    if (this.isCurrent(generation)) this.generation += 1;
  }

  invalidateLoad() {
    this.generation += 1;
  }

  markSnapDirty() {
    this.snapDirty = true;
  }

  consumeSnapChanges() {
    const dirty = this.snapDirty;
    this.snapDirty = false;
    return dirty;
  }

  matches(feature: Feature, raw: RawViewportFeature, representation: string) {
    const stamp = this.stamps.get(feature);
    return (
      stamp?.representation === representation &&
      stamp.featureRevision === feature.getRevision() &&
      featureContentMatches(stamp.content, raw, feature.getGeometry())
    );
  }

  remember(feature: Feature, raw: RawViewportFeature, representation: string) {
    this.stamps.set(feature, {
      content: captureFeatureContent(raw),
      representation,
      featureRevision: feature.getRevision(),
    });
    return this.set(String(feature.getId()), feature);
  }
}
