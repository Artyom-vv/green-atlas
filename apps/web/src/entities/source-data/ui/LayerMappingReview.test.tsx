import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
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
  it('confirms the detailed proposal instead of the old broad role', () => {
    const source = { ...layer('grass', 'building'), mapped_kind: 'existing_green' as const };
    const mapping: LayerMapping = { layer_id: source.id, kind: 'existing_green', confirmed: false, visible: true };
    const recognition: LayerRecognition = {
      source_sha256: null, provider: 'openai/gpt-6-luna', status: 'completed',
      categories: [{category: 'lawn', kind: 'lawn', label: 'Газон'}],
      proposals: [{layer_id: source.id, category: 'lawn', confidence: 'high', evidence: ['Газон'], unresolved: []}],
      processed_count: 1, total_count: 1,
    };
    const onChange = vi.fn();
    render(<LayerMappingReview layers={[source]} mappings={{grass: mapping}} recognition={recognition} onChange={onChange} showComposition={false} />);
    expect(screen.getByText('Газон', { selector: 'div.font-semibold' })).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: 'Подтвердить'}));
    expect(onChange).toHaveBeenCalledWith({grass: {...mapping, kind: 'lawn', category: 'lawn', confirmed: true}});
  });
  it('does not overwrite an already confirmed decision with a model proposal', () => {
    const source = layer('building', 'building');
    const mapping: LayerMapping = {layer_id: source.id, kind: 'building', confirmed: true, visible: true};
    const recognition: LayerRecognition = {source_sha256: null, provider: 'model', status: 'completed', processed_count: 1, total_count: 1,
      categories: [{category: 'lawn', kind: 'lawn', label: 'Газон'}],
      proposals: [{layer_id: source.id, category: 'lawn', confidence: 'high', evidence: [], unresolved: []}]};
    const onChange = vi.fn();
    render(<LayerMappingReview layers={[source]} mappings={{building: mapping}} recognition={recognition} onChange={onChange} showComposition={false} />);
    expect(screen.queryByText('Газон', { selector: 'div.font-semibold' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Подтвердить'}));
    expect(onChange).toHaveBeenCalledWith({building: mapping});
  });
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
    const unknown = {
      ...layer('0', 'building'),
      mapped_kind: 'ignore' as const,
    };
    const onChange = vi.fn();
    render(
      <LayerMappingReview
        layers={[unknown]}
        mappings={{
          '0': {
            layer_id: '0',
            kind: 'ignore',
            confirmed: false,
            visible: true,
          },
        }}
        onChange={onChange}
      />,
    );
    expect(screen.getByText('Тип не определён')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /^Подтвердить$/ }),
    ).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
  it('does not bulk-confirm a descriptive category without a calculation role', () => {
    const source = layer('mixed', 'building');
    const onChange = vi.fn();
    const recognition: LayerRecognition = {
      source_sha256: null, provider: 'openai/gpt-6-luna', status: 'completed',
      processed_count: 1, total_count: 1,
      categories: [{ category: 'mixed_source', kind: null, label: 'Смешанные объекты' }],
      proposals: [{ layer_id: 'mixed', category: 'mixed_source', confidence: 'high', evidence: [], unresolved: [] }],
    };
    render(<LayerMappingReview
      layers={[source]}
      mappings={{ mixed: { layer_id: 'mixed', kind: 'building', confirmed: false, visible: true } }}
      recognition={recognition}
      onChange={onChange}
    />);
    expect(screen.getByText('Расчётная роль не установлена')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Подтвердить' })).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
