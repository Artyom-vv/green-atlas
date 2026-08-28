import { describe, expect, it } from 'vitest';
import { applySelection } from './useWorkspaceEditor';

describe('workspace selection set', () => {
  it('replaces, adds, subtracts and toggles without duplicate ids', () => {
    expect(applySelection(['a'], ['b', 'b'], 'replace')).toEqual(['b']);
    expect(applySelection(['a'], ['b', 'a'], 'add')).toEqual(['a', 'b']);
    expect(applySelection(['a', 'b', 'c'], ['b', 'x'], 'subtract')).toEqual(['a', 'c']);
    expect(applySelection(['a', 'b'], ['b', 'c'], 'toggle')).toEqual(['a', 'c']);
  });
});
