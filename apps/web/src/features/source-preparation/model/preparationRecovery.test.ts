import { describe, expect, it } from 'vitest';
import { EMPTY_UTILITY_CONTEXT } from '@/entities/source-data/model/layerKinds';
import { mappingKey } from './preparationRecovery';

describe('mapping draft identity', () => {
  it('detects confirmation, category and network changes independently', () => {
    const mapping = {
      layer_id: 'layer',
      kind: 'utility' as const,
      confirmed: false,
      visible: true,
    };
    const before = mappingKey([mapping]);
    expect(mappingKey([{ ...mapping, confirmed: true }])).not.toBe(before);
    expect(mappingKey([{ ...mapping, category: 'water_network' }])).not.toBe(
      before,
    );
    expect(
      mappingKey([{ ...mapping, utility_context: EMPTY_UTILITY_CONTEXT }]),
    ).not.toBe(before);
  });
});
