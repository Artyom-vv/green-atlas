import { describe, expect, it } from 'vitest';
import { applySelection, useWorkspaceEditor } from './useWorkspaceEditor';
import { act, renderHook } from '@testing-library/react';
import type { ChangeSetPreview } from '@green/api-client';

describe('workspace selection set', () => {
  it('preserves a draft while browsing selections or choosing the same tool', () => {
    const { result, rerender } = renderHook(({ id }) => useWorkspaceEditor(id), { initialProps: { id: 'project-a' } });
    const preview = { id: 'draft', base_plan_version: 1, digest: 'digest', label: 'Кисть', source: 'manual', can_apply: true } as ChangeSetPreview;
    act(() => { result.current.setTool('brush'); result.current.setPreview(preview); });
    act(() => { result.current.select(['a']); result.current.setTool('brush'); });
    expect(result.current.preview).toBe(preview);
    act(() => result.current.clearSelection());
    expect(result.current.preview).toBe(preview);
    rerender({ id: 'project-b' });
    expect(result.current.preview).toBeUndefined();
    expect(result.current.tool).toBe('select');
  });
  it('replaces, adds, subtracts and toggles without duplicate ids', () => {
    expect(applySelection(['a'], ['b', 'b'], 'replace')).toEqual(['b']);
    expect(applySelection(['a'], ['b', 'a'], 'add')).toEqual(['a', 'b']);
    expect(applySelection(['a', 'b', 'c'], ['b', 'x'], 'subtract')).toEqual(['a', 'c']);
    expect(applySelection(['a', 'b'], ['b', 'c'], 'toggle')).toEqual(['a', 'c']);
  });
});
