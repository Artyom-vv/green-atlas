import { describe, expect, it } from 'vitest';
import { EMPTY_UTILITY_CONTEXT } from '@/entities/source-data/model/layerKinds';
import { mappingKey } from './preparationRecovery';

describe('mapping draft identity', () => {
  it('ignores utility property and layer order while preserving native null confirmation', () => {
    const mapping = {
      layer_id: 'network',
      kind: 'utility' as const,
      confirmed: null,
      visible: true,
      utility_context: EMPTY_UTILITY_CONTEXT,
    };
    const reordered = Object.fromEntries(
      Object.entries(EMPTY_UTILITY_CONTEXT).reverse(),
    ) as typeof EMPTY_UTILITY_CONTEXT;
    expect(mappingKey([mapping])).toBe(
      mappingKey([{ ...mapping, utility_context: reordered }]),
    );
    expect(mappingKey([mapping])).not.toBe(
      mappingKey([{ ...mapping, confirmed: true }]),
    );
    const second = { ...mapping, layer_id: 'other' };
    expect(mappingKey([mapping, second])).toBe(mappingKey([second, mapping]));
  });
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

describe('preparation operation recovery order', () => {
  it('keeps a running calculation visible when a capture operation is newer', async () => {
    const { latestPreparationOperation } = await import('./sourcePreparation');
    const calculation = {
      id: 'calculation',
      project_id: 'project',
      kind: 'calculate_geometry' as const,
      status: 'running' as const,
      created_at: '2026-10-04T10:00:00Z',
      stage: 'Подготовка',
      project_state_version: 1,
      progress: 10,
      progress_mode: 'determinate' as const,
    };
    const capture = {
      ...calculation,
      id: 'capture',
      kind: 'prepare_cad_capture' as const,
      status: 'completed' as const,
      created_at: '2026-10-04T11:00:00Z',
    };
    expect(latestPreparationOperation(calculation, capture)).toBe(calculation);
    expect(
      latestPreparationOperation(
        { ...calculation, status: 'failed' },
        { ...capture, status: 'running' },
      ),
    ).toMatchObject({ id: 'capture' });
    expect(
      latestPreparationOperation(
        { ...calculation, status: 'completed' },
        capture,
      ),
    ).toBe(capture);
  });
});
