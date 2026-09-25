import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Layer, LayerMapping } from '@green/api-client';
import { LayerMappingReview } from './LayerMappingReview';

function layer(id: string, kind: 'utility' | 'building'): Layer {
  return {
    id,
    source_name: id,
    suggested_kind: kind,
    mapped_kind: kind,
    suggestion_confidence: 'medium',
    mapping_review_required: true,
    mapping_confirmed: false,
    object_count: 1,
    color: '#111111',
    linetype: 'CONTINUOUS',
    geometry_complete: true,
    required: false,
    visible: true,
  };
}

describe('LayerMappingReview', () => {
  afterEach(cleanup);
  it('confirms one semantic group without creating an action for every layer', () => {
    const layers = [
      layer('network-a', 'utility'),
      layer('network-b', 'utility'),
    ];
    const mappings: Record<string, LayerMapping> = Object.fromEntries(
      layers.map((item) => [
        item.id,
        {
          layer_id: item.id,
          kind: item.mapped_kind!,
          confirmed: false,
          visible: true,
        },
      ]),
    );
    const onChange = vi.fn();

    render(
      <LayerMappingReview
        layers={layers}
        mappings={mappings}
        onChange={onChange}
      />,
    );

    expect(screen.getByText('2 слоя')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Подтвердить' })).toHaveLength(
      1,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
    expect(onChange).toHaveBeenCalledWith({
      'network-a': { ...mappings['network-a'], confirmed: true },
      'network-b': { ...mappings['network-b'], confirmed: true },
    });
  });
  it('never offers a bulk confirmation of unidentified layers', () => {
    const unknown = { ...layer('0', 'building'), mapped_kind: 'ignore' as const };
    const onChange = vi.fn();
    render(<LayerMappingReview layers={[unknown]}
      mappings={{ '0': { layer_id: '0', kind: 'ignore', confirmed: false, visible: true } }}
      onChange={onChange} />);
    expect(screen.getByRole('cell', { name: 'Тип не определён' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Подтвердить$/ })).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
