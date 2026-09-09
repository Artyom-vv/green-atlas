import { describe, expect, it } from 'vitest';
import type { ChangeSetPreview, PlanObject } from '@green/api-client';
import Feature from 'ol/Feature';
import Circle from 'ol/geom/Circle';
import LineString from 'ol/geom/LineString';
import Point from 'ol/geom/Point';
import { Circle as CircleStyle, Icon, type Style } from 'ol/style';
import { brushCursorStyle, brushStrokeStyle, changePreviewFeatures, changePreviewStyle, planStyle, selectionDraftStyle } from './MapViewport';

const object = (status: PlanObject['status'] = 'valid'): PlanObject => ({
  id: 'tree-1',
  kind: 'tree',
  x: 10,
  y: 20,
  radius: 1.6,
  layout_radius_m: 1.6,
  size_class: 'standard',
  locked: false,
  status,
  spacing_policy: 'balanced',
});

const planFeature = (status: PlanObject['status']) => new Feature({
  geometry: new Circle([10, 20], 1.6),
  markerGeometry: new Point([10, 20]),
  objectId: 'tree-1',
  kind: 'tree',
  status,
});

const preview = (update: PlanObject): ChangeSetPreview => ({
  id: 'preview-1',
  digest: 'digest',
  base_plan_version: 1,
  source: 'group',
  label: 'Перенос группы',
  can_apply: false,
  updates: [update],
  candidate_results: [{
    operation_index: 0,
    type: 'update',
    status: 'blocked',
    code: 'CONSTRAINT',
    category: 'constraint',
    reason: 'Конфликт с ограничением',
    object_id: update.id,
  }],
  expires_at: '2030-01-01T00:00:00Z',
});

describe('plan marker visual semantics', () => {
  it('keeps validation color distinct from selection at detail scale', () => {
    const colors = (['valid', 'warning', 'error'] as const).map((status) => {
      const styles = planStyle(planFeature(status), new Set(['tree-1']), 0.25) as Style[];
      const semanticCrown = styles.find((style) => style.getStroke()?.getLineDash()?.length);
      const selectionRing = styles.find((style) => {
        const image = style.getImage();
        return image instanceof CircleStyle && image.getStroke()?.getColor() === '#225CFF';
      });
      expect(selectionRing).toBeDefined();
      return semanticCrown?.getStroke()?.getColor();
    });

    expect(colors).toEqual(['#168A5B', '#B76400', '#D92D20']);
  });

  it('uses a quiet fixed-size marker at overview scale', () => {
    const styles = planStyle(planFeature('error'), new Set(['tree-1']), 2) as Style[];
    const markerImages = styles.map((style) => style.getImage()).filter((image): image is CircleStyle => image instanceof CircleStyle);

    expect(markerImages.some((image) => image.getRadius() === 8)).toBe(true);
    expect(markerImages.some((image) => image.getRadius() === 4.5 && image.getFill()?.getColor() === '#D92D20')).toBe(true);
    expect(styles.some((style) => style.getImage() instanceof Icon)).toBe(false);
  });

  it('temporarily uses live move validation without replacing persisted status', () => {
    const feature = planFeature('valid');
    feature.set('_liveCandidateStatus', 'blocked');
    const styles = planStyle(feature, new Set(['tree-1']), 2) as Style[];
    const statusMarker = styles.find((style) => {
      const image = style.getImage();
      return image instanceof CircleStyle && image.getFill()?.getColor() === '#D92D20';
    });

    expect(statusMarker).toBeDefined();
    expect(feature.get('status')).toBe('valid');
  });
});

describe('move preview visual semantics', () => {
  it('marks only proposed deletions, without removing saved objects', () => {
    const saved = object();
    const change = { ...preview(saved), updates: [], deletion_ids: ['tree-1', 'missing'], can_apply: true };
    const features = changePreviewFeatures([saved], change);
    expect(features).toHaveLength(1);
    expect(features[0].get('previewRole')).toBe('delete');
    expect((features[0].getGeometry() as Point).getCoordinates()).toEqual([10, 20]);
    const marker = changePreviewStyle(features[0])[0].getImage() as Icon;
    expect(decodeURIComponent(marker.getSrc() ?? '')).toContain('#C83B32');
    expect(saved.status).toBe('valid');
  });
  it('keeps a rejected copy visible without adding it to the applicable server preview', () => {
    const rejected = { ...preview(object()), additions: [], updates: [], can_apply: false, candidate_results: [{ operation_index: 0, type: 'add' as const, status: 'blocked' as const, code: 'CONSTRAINT', category: 'constraint' as const, reason: 'Занято существующим озеленением' }] };
    const draft = { base_plan_version: 1, source: 'group' as const, policy: 'all_or_nothing' as const, label: 'Копирование посадки', operations: [{ type: 'add' as const, object: { kind: 'tree' as const, x: 30, y: 40, radius: 1.6, size_class: 'standard' as const, spacing_policy: 'balanced' as const, group_ids: [], locked: false } }] };
    const features = changePreviewFeatures([], rejected, draft);
    expect(features).toHaveLength(1);
    expect((features[0].getGeometry() as Circle).getCenter()).toEqual([30, 40]);
    expect(features[0].get('candidateStatus')).toBe('blocked');
    expect(rejected.additions).toEqual([]);
    expect(rejected.can_apply).toBe(false);
    expect(changePreviewFeatures([], rejected)).toEqual([]);
  });
  it('connects the original point to the validated destination', () => {
    const update = { ...object('valid'), x: 35, y: 42 };
    const features = changePreviewFeatures([object('valid')], preview(update));

    expect(features.map((feature) => feature.get('previewRole'))).toEqual(['move-path', 'move-origin', 'candidate']);
    expect((features[0].getGeometry() as LineString).getCoordinates()).toEqual([[10, 20], [35, 42]]);
    expect((features[1].getGeometry() as Point).getCoordinates()).toEqual([10, 20]);
    expect((features[2].getGeometry() as Circle).getCenter()).toEqual([35, 42]);
    expect(features[2].getProperties()).toMatchObject({
      candidateCode: 'CONSTRAINT',
      candidateReason: 'Конфликт с ограничением',
    });

    const candidateStyles = changePreviewStyle(features[2], 2);
    const statusMarker = candidateStyles.find((style) => {
      const image = style.getImage();
      return image instanceof CircleStyle && image.getFill()?.getColor() === '#D92D20';
    });
    expect(statusMarker).toBeDefined();
    expect(changePreviewStyle(features[0], 2)[1].getStroke()?.getColor()).toBe('#D92D20');
  });

  it('uses one aggregate trail for a large rigid group move', () => {
    const objects = Array.from({ length: 70 }, (_, index) => ({
      ...object('valid'),
      id: `tree-${index}`,
      x: index,
      y: index * 2,
    }));
    const updates = objects.map((item) => ({ ...item, x: item.x + 10, y: item.y + 5 }));
    const groupPreview = {
      ...preview(updates[0]),
      updates,
      candidate_results: updates.map((item, operation_index) => ({
        operation_index,
        type: 'update' as const,
        status: 'allowed' as const,
        code: 'OK',
        category: 'constraint' as const,
        reason: 'Допустимо',
        object_id: item.id,
      })),
    };

    const features = changePreviewFeatures(objects, groupPreview);

    expect(features.filter((feature) => feature.get('previewRole') === 'move-path')).toHaveLength(1);
    expect(features.filter((feature) => feature.get('previewRole') === 'move-origin')).toHaveLength(1);
    expect(features.filter((feature) => feature.get('previewRole') === 'candidate')).toHaveLength(70);
  });

  it('does not add a move trail to an unchanged preview object', () => {
    expect(changePreviewFeatures([object()], preview(object())).map((feature) => feature.get('previewRole'))).toEqual(['candidate']);
  });

  it('renders live drag paths above a white separation stroke', () => {
    const feature = new Feature({ geometry: new LineString([[10, 20], [12, 24]]), draftRole: 'move-path' });
    const styles = selectionDraftStyle(feature) as Style[];

    expect(styles).toHaveLength(2);
    expect(styles[0].getStroke()?.getColor()).toBe('rgba(255,255,255,.94)');
    expect(styles[1].getStroke()?.getColor()).toBe('#225CFF');
  });
});

describe('brush visual feedback', () => {
  it('shows the real diameter before drawing and keeps completed strokes legible', () => {
    const cursor = new Feature({ geometry: new Circle([10, 20], 6), brushMode: 'add' });
    const stroke = new Feature({ geometry: new LineString([[0, 0], [20, 0]]), brushMode: 'subtract', brushWidthM: 12 });

    expect((cursor.getGeometry() as Circle).getRadius()).toBe(6);
    expect(brushCursorStyle(cursor)[1].getStroke()?.getColor()).toBe('#225CFF');
    expect(brushStrokeStyle(stroke, 1)[0].getStroke()?.getWidth()).toBe(12);
    expect(brushStrokeStyle(stroke, 1)[1].getStroke()?.getColor()).toBe('#D92D20');
  });
});
