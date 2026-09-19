import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Layer, LayerMapping } from '@green/api-client';
import { LayerMappingTable } from './LayerMappingTable';

const layer: Layer = {
  id: 'network',
  source_name: 'СУЩ_СЕТИ',
  suggested_kind: 'utility',
  suggestion_confidence: 'medium',
  suggestion_reasons: ['Название слоя соответствует роли'],
  mapping_review_required: true,
  mapping_confirmed: false,
  mapped_kind: 'utility',
  object_count: 12,
  color: '#111111',
  linetype: 'CONTINUOUS',
  geometry_complete: true,
  required: false,
  visible: true,
};

describe('LayerMappingTable', () => {
  it('keeps an uncertain automatic role pending until the operator confirms it', () => {
    const mapping: LayerMapping = {
      layer_id: layer.id,
      kind: 'utility',
      confirmed: false,
      visible: true,
    };
    const onChange = vi.fn();

    render(
      <LayerMappingTable
        layers={[layer]}
        mappings={{ [layer.id]: mapping }}
        onChange={onChange}
      />,
    );

    expect(screen.getByText('проверьте роль')).toHaveAttribute(
      'title',
      'Название слоя соответствует роли',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
    expect(onChange).toHaveBeenCalledWith({
      [layer.id]: { ...mapping, confirmed: true },
    });
  });
});
