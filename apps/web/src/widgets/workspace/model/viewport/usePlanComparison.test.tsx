import { createEditorStore } from '@/entities/editor';
import { act, renderHook } from '@testing-library/react';
import { useStore } from 'zustand';
import { describe, expect, it } from 'vitest';
import { usePlanComparison } from './usePlanComparison';

describe('saved plan comparison', () => {
  it('preserves the editing tool, selection and source visibility through both views', () => {
    const store = createEditorStore('project');
    store.getState().setTool('pattern_row');
    store.getState().select(['tree']);
    store.getState().setVisibility({ layer: false });
    const { result } = renderHook(() => {
      const editor = useStore(store);
      return usePlanComparison({
        mode: editor.planComparison,
        setMode: editor.setPlanComparison,
        available: true,
        blocked: false,
      });
    });
    act(() => result.current.change('after'));
    expect(result.current).toMatchObject({ active: true, mode: 'after' });
    act(() => result.current.change('before'));
    expect(result.current.savedPlantingsVisible).toBe(false);
    act(() => result.current.change('editing'));
    expect(store.getState()).toMatchObject({
      planComparison: 'editing',
      tool: 'pattern_row',
      selectedIds: ['tree'],
      visibility: { layer: false },
    });
  });

  it('blocks entering while a write or drawing gesture owns the editor, and always permits returning', () => {
    const store = createEditorStore('project');
    const { result, rerender } = renderHook(
      ({ blocked }) => {
        const editor = useStore(store);
        return usePlanComparison({
          mode: editor.planComparison,
          setMode: editor.setPlanComparison,
          available: true,
          blocked,
        });
      },
      { initialProps: { blocked: true } },
    );
    act(() => result.current.change('after'));
    expect(result.current.mode).toBe('editing');
    rerender({ blocked: false });
    act(() => result.current.change('after'));
    rerender({ blocked: true });
    act(() => result.current.change('before'));
    expect(result.current.mode).toBe('after');
    act(() => result.current.change('editing'));
    expect(result.current.active).toBe(false);
  });

  it('keeps comparison scoped to the mounted project and unavailable without a prepared saved plan', () => {
    const first = createEditorStore('first');
    const second = createEditorStore('second');
    const { result } = renderHook(() =>
      usePlanComparison({
        mode: first.getState().planComparison,
        setMode: first.getState().setPlanComparison,
        available: false,
        blocked: false,
      }),
    );
    act(() => result.current.change('before'));
    expect(first.getState().planComparison).toBe('editing');
    first.getState().setPlanComparison('after');
    expect(second.getState().planComparison).toBe('editing');
  });
});
