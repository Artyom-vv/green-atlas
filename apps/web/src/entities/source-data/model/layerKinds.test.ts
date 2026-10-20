import { describe, expect, it } from 'vitest';
import type { Layer, LayerMapping } from '@green/api-client';
import {
  changeLayerRole,
  EMPTY_UTILITY_CONTEXT,
  toLayerMapping,
  isBoundaryTopologyIntent,
  requiresLayerRoleReview,
} from './layerKinds';

describe('layer interpretation persistence', () => {
  it('separates boundary preparation intent from whole-layer physical confirmation', () => {
    const boundary: LayerMapping = {
      layer_id: 'boundary',
      kind: 'ignore',
      confirmed: false,
      category: 'surface_boundary',
      visible: true,
    };
    expect(isBoundaryTopologyIntent(boundary)).toBe(true);
    expect(requiresLayerRoleReview(boundary)).toBe(false);
    expect(boundary.confirmed).toBe(false);
    expect(isBoundaryTopologyIntent({ ...boundary, confirmed: true })).toBe(
      false,
    );
    expect(isBoundaryTopologyIntent({ ...boundary, kind: 'road' })).toBe(false);
    expect(requiresLayerRoleReview({ ...boundary, kind: 'road' })).toBe(true);
    expect(
      requiresLayerRoleReview({ ...boundary, category: 'mixed_source' }),
    ).toBe(true);
  });
  it('does not discard false confirmations when review_required is false', () => {
    const layer = {
      id: 'layer',
      mapped_kind: 'ignore',
      mapping_confirmed: false,
      mapping_review_required: false,
    } as Layer;
    expect(toLayerMapping(layer).confirmed).toBe(false);
  });
  it('preserves explicit unknown confirmation even when review is required', () => {
    const layer = {
      id: 'legacy-layer',
      mapped_kind: 'utility',
      mapping_confirmed: null,
      mapping_review_required: false,
    } as Layer;
    expect(toLayerMapping(layer).confirmed).toBeNull();
    expect(
      toLayerMapping({ ...layer, mapping_review_required: true }).confirmed,
    ).toBeNull();
    expect(
      toLayerMapping({ ...layer, mapping_confirmed: true }).confirmed,
    ).toBe(true);
  });
  it('keeps the legacy fallback for an omitted confirmation with a required review', () => {
    const layer = {
      id: 'legacy-layer',
      mapped_kind: 'utility',
      mapping_review_required: true,
    } as Layer;
    expect(toLayerMapping(layer).confirmed).toBe(false);
  });
  it('does not turn a reviewer-only edit into role confirmation in the serialized write', () => {
    const layer = {
      id: 'legacy-layer',
      mapped_kind: 'utility',
      mapping_confirmed: null,
      mapping_review_required: true,
      visible: true,
      utility_context: EMPTY_UTILITY_CONTEXT,
    } as Layer;
    const mapping = toLayerMapping(layer);
    const reviewerOnlyEdit = {
      ...mapping,
      utility_context: {
        ...mapping.utility_context,
        confirmed_by: 'Проектировщик',
      },
    };
    const payload = JSON.stringify({ mappings: [reviewerOnlyEdit] });
    // Omission means legacy automatic confirmation on the server; null preserves unknown.
    expect(JSON.parse(payload).mappings[0]).toMatchObject({
      layer_id: 'legacy-layer',
      confirmed: null,
      utility_context: { confirmed_by: 'Проектировщик' },
    });
  });
  it('does not carry network confirmation across a changed network type', () => {
    const context = {
      ...EMPTY_UTILITY_CONTEXT,
      network_type: 'water' as const,
      review_status: 'confirmed' as const,
    };
    const next = changeLayerRole(
      {
        layer_id: 'layer',
        kind: 'utility',
        utility_context: context,
        visible: true,
      },
      'utility',
      'gas_network',
    );
    expect(next.utility_context).toMatchObject({
      network_type: 'gas',
      review_status: 'unconfirmed',
    });
    expect(
      changeLayerRole(next, 'building', 'building').utility_context,
    ).toBeNull();
  });
});
