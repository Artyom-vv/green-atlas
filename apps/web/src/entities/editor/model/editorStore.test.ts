import { createEditorStore, applySelection } from '@/entities/editor';
import { describe, expect, it } from 'vitest';
describe('editor session', () => {
  it('opens the source layers tab without closing the inspector or changing another project', () => {
    const store = createEditorStore('a');
    const other = createEditorStore('b');
    store.getState().setLeftOpen(false);
    store.getState().openSourceLayers();
    expect(store.getState()).toMatchObject({
      resourcesTab: 'layers',
      leftOpen: true,
      rightOpen: true,
    });
    expect(other.getState()).toMatchObject({
      resourcesTab: 'project',
      leftOpen: true,
    });
    store.getState().setResourcesTab('project');
    expect(store.getState().resourcesTab).toBe('project');
  });
  it('keeps project stores independent', () => {
    const first = createEditorStore('a');
    const second = createEditorStore('b');
    first.getState().select(['object-a']);
    first.getState().setTool('brush');
    expect(second.getState()).toMatchObject({
      projectId: 'b',
      tool: 'select',
      selectedIds: [],
    });
  });
  it('replaces, adds, subtracts and toggles without duplicate ids', () => {
    expect(applySelection(['a'], ['b', 'b'], 'replace')).toEqual(['b']);
    expect(applySelection(['a'], ['b', 'a'], 'add')).toEqual(['a', 'b']);
    expect(applySelection(['a', 'b', 'c'], ['b', 'x'], 'subtract')).toEqual([
      'a',
      'c',
    ]);
    expect(applySelection(['a', 'b'], ['b', 'c'], 'toggle')).toEqual([
      'a',
      'c',
    ]);
  });

  it('isolates project views and source visibility with the established defaults', () => {
    const first = createEditorStore('a');
    const second = createEditorStore('b');
    expect(first.getState().visibility).not.toBe(second.getState().visibility);
    first.getState().setPanel('zones');
    first.getState().setIdeRightTab('assistant');
    first.getState().setLeftOpen(false);
    first.getState().setRightOpen(false);
    first.getState().setResultsOpen(true);
    first.getState().setResultsTab('history');
    first.getState().setActiveLayerId('source-a');
    first.getState().setVisibility({ 'source-a': false });
    expect(second.getState()).toMatchObject({
      panel: null,
      ideRightTab: 'inspector',
      leftOpen: true,
      rightOpen: true,
      resultsOpen: false,
      resultsTab: 'issues',
      activeLayerId: undefined,
      visibility: {},
    });
  });

  it('applies successive updater callbacks to the current store without a render copy', () => {
    const store = createEditorStore('a');
    const actions = store.getState();
    const originalVisibility = actions.visibility;
    actions.setPanel((previous) => (previous === null ? 'plantings' : null));
    actions.setIdeRightTab((previous) =>
      previous === 'inspector' ? 'tool' : 'inspector',
    );
    actions.setLeftOpen((previous) => !previous);
    actions.setRightOpen((previous) => !previous);
    actions.setResultsOpen((previous) => !previous);
    actions.setResultsOpen((previous) => !previous);
    actions.setResultsTab((previous) =>
      previous === 'issues' ? 'schedule' : 'issues',
    );
    actions.setActiveLayerId((previous) => previous ?? 'layer-a');
    actions.setActiveLayerId((previous) =>
      previous === 'layer-a' ? undefined : previous,
    );
    actions.setVisibility((previous) => ({ ...previous, 'layer-a': false }));
    actions.setVisibility((previous) => ({ ...previous, 'layer-b': false }));
    expect(store.getState()).toMatchObject({
      panel: 'plantings',
      ideRightTab: 'tool',
      leftOpen: false,
      rightOpen: false,
      resultsOpen: false,
      resultsTab: 'schedule',
      activeLayerId: undefined,
      visibility: { 'layer-a': false, 'layer-b': false },
    });
    expect(originalVisibility).toEqual({});
    expect(store.getState().setVisibility).toBe(actions.setVisibility);
  });

  it('resets the editing gesture and selection while retaining every view preference', () => {
    const store = createEditorStore('a');
    const actions = store.getState();
    actions.setPanel('zones');
    actions.setIdeRightTab('assistant');
    actions.setLeftOpen(false);
    actions.setRightOpen(false);
    actions.setResultsOpen(true);
    actions.setResultsTab('history');
    actions.setActiveLayerId('layer-a');
    actions.setVisibility({ 'layer-a': false });
    actions.setTool('brush');
    actions.select(['tree-1']);
    const beforeReset = store.getState();
    actions.reset();
    expect(store.getState()).toEqual({
      ...beforeReset,
      tool: 'select',
      selectedIds: [],
    });
  });
});
