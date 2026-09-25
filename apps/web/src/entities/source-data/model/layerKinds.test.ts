import { describe, expect, it } from 'vitest';
import type { Layer } from '@green/api-client';
import {
  changeLayerRole,
  EMPTY_UTILITY_CONTEXT,
  toLayerMapping,
} from './layerKinds';

describe('layer interpretation persistence', () => {
  it('does not discard false confirmations when review_required is false', () => {
    const layer = {
      id: 'layer',
      mapped_kind: 'ignore',
      mapping_confirmed: false,
      mapping_review_required: false,
    } as Layer;
    expect(toLayerMapping(layer).confirmed).toBe(false);
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
