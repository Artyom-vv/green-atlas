import { describe, expect, it } from 'vitest';
import type { LayerMapping } from '@green/api-client';
import { mappingImpact } from './mappingImpact';

const saved: LayerMapping = {
  layer_id: 'network',
  kind: 'utility',
  confirmed: true,
  visible: true,
  utility_context: {
    network_type: 'gas',
    geometry_reference: 'outer_surface',
    installation: 'underground',
    review_status: 'confirmed',
    confirmed_by: 'Reviewer',
    source_reference: 'Sheet',
  },
};

describe('mapping impact', () => {
  it.each([
    'confirmed_by',
    'source_reference',
    'network_type',
    'geometry_reference',
    'installation',
    'review_status',
  ] as const)('keeps %s corrections local', (field) => {
    const changed = {
      ...saved,
      utility_context: { ...saved.utility_context!, [field]: 'changed' },
    } as LayerMapping;
    expect(mappingImpact([saved], [changed])).toEqual({
      physicalChanged: false,
      metadataChanged: true,
      displayChanged: false,
    });
  });
  it.each([
    { kind: 'restricted' as const },
    { category: 'gas_network' as const },
    { confirmed: false },
    { confirmed: null },
    { utility_axis_bindings: [{ object_key: 'axis' }] },
  ])('requires preparation when physical input changes: %j', (patch) => {
    const changed = { ...saved, ...patch } as LayerMapping;
    expect(mappingImpact([saved], [changed]).physicalChanged).toBe(true);
  });
  it('does not prepare CAD for a visibility correction', () => {
    expect(mappingImpact([saved], [{ ...saved, visible: false }])).toEqual({
      physicalChanged: false,
      metadataChanged: false,
      displayChanged: true,
    });
  });
});
