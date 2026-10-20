import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { LayerMapping } from '@green/api-client';
import { useMappingUndo } from './useMappingUndo';

const saved: Record<string, LayerMapping> = {
  a: { layer_id: 'a', kind: 'building', visible: true, confirmed: true },
};
describe('local mapping undo', () => {
  it('restores the whole mapping including utility context and axis bindings', () => {
    const original: Record<string, LayerMapping> = {
      a: {
        ...saved.a,
        kind: 'utility',
        utility_axis_bindings: [
          {
            id: 'axis-a',
            source: {
              content_sha256: 'a'.repeat(64),
              feature_id: 'feature-a',
              source_handle: 'A1',
              insert_chain: [],
              geometry_sha256: 'b'.repeat(64),
            },
            context: {
              network_type: 'sewer',
              geometry_reference: 'axis',
              installation: 'underground',
              source_reference: 'CAD',
              confirmed_by: 'Engineer',
              review_status: 'confirmed',
            },
            outside_diameter_m: 0.4,
            surface_reference: 'outer_surface',
            outside_size_reference: 'CAD',
            envelope_model: 'circular_sweep',
            endpoint_model: 'round_full_source_extent',
            extent_reference: 'Full source extent',
          },
        ],
        utility_context: {
          network_type: 'sewer',
          geometry_reference: 'axis',
          installation: 'underground',
          source_reference: 'CAD',
          confirmed_by: 'Engineer',
          review_status: 'confirmed',
        },
      },
    };
    const onChange = vi.fn();
    const { result, rerender } = renderHook(
      ({ mappings }) =>
        useMappingUndo({
          source: 'source-a',
          mappings,
          savedMappings: saved,
          onChange,
        }),
      { initialProps: { mappings: original } },
    );
    const next = { a: { ...saved.a, kind: 'road' as const } };
    act(() => result.current.change(next));
    rerender({ mappings: next });
    expect(result.current.canUndo).toBe(true);
    act(() => result.current.undo());
    expect(onChange).toHaveBeenLastCalledWith(original);
    expect(result.current.canUndo).toBe(false);
  });
  it('discards history permanently when returning to a previously opened source', () => {
    const onChange = vi.fn();
    const { result, rerender } = renderHook(
      (props) => useMappingUndo({ ...props, onChange }),
      {
        initialProps: {
          source: 'source-a',
          mappings: saved,
          savedMappings: saved,
        },
      },
    );
    act(() => result.current.change({ a: { ...saved.a, kind: 'road' } }));
    expect(result.current.canUndo).toBe(true);
    rerender({ source: 'source-b', mappings: saved, savedMappings: saved });
    rerender({ source: 'source-a', mappings: saved, savedMappings: saved });
    expect(result.current.canUndo).toBe(false);
  });
  it('cannot undo into a previous source or across an acknowledged save', () => {
    const onChange = vi.fn();
    const changed = { a: { ...saved.a, kind: 'road' as const } };
    const { result, rerender } = renderHook(
      (props) => useMappingUndo({ ...props, onChange }),
      {
        initialProps: {
          source: 'source-a',
          mappings: saved,
          savedMappings: saved,
        },
      },
    );
    act(() => result.current.change(changed));
    rerender({ source: 'source-a', mappings: changed, savedMappings: changed });
    expect(result.current.canUndo).toBe(false);
    act(() => result.current.undo());
    expect(onChange).toHaveBeenCalledTimes(1);
    rerender({ source: 'source-b', mappings: saved, savedMappings: saved });
    expect(result.current.canUndo).toBe(false);
    rerender({ source: 'source-a', mappings: saved, savedMappings: saved });
    expect(result.current.canUndo).toBe(false);
  });
});
