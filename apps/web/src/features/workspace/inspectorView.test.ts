import { expect, it } from 'vitest';
import { resolveInspectorView } from './inspectorView';
const base = { panel: null, hasPlan: true, sourcePreview: false, hasLayer: false, tool: 'select' as const, drawingPlacementArea: false, hasPattern: false, hasChange: false, hasRecommendation: false, assigningSpecies: false, selectionCount: 0, hasArea: false };
it('keeps object properties in the inspector while creation tools have their own surfaces', () => {
  expect(resolveInspectorView({ ...base, tool: 'brush', hasArea: true, selectionCount: 30 })).toBe('group');
  expect(resolveInspectorView({ ...base, tool: 'pattern_row', hasArea: true, selectionCount: 1 })).toBe('object');
});
it('can inspect a layer or result without replacing the underlying task state', () => {
  expect(resolveInspectorView({ ...base, tool: 'brush', hasLayer: true })).toBe('layer');
  expect(resolveInspectorView({ ...base, panel: 'export', selectionCount: 1 })).toBe('object');
  expect(resolveInspectorView({ ...base, hasChange: true, selectionCount: 30 })).toBe('group');
  expect(resolveInspectorView({ ...base, hasChange: true, assigningSpecies: true, selectionCount: 30 })).toBe('group');
  expect(resolveInspectorView({ ...base, tool: 'pattern_fill', hasRecommendation: true, hasChange: false })).toBe('overview');
  expect(resolveInspectorView({ ...base, tool: 'pattern_fill', recommendationOpen: true })).toBe('overview');
});
it('gives explicit assignment and zone management a single unambiguous surface', () => {
  expect(resolveInspectorView({ ...base, tool: 'brush', assigningSpecies: true })).toBe('overview');
  expect(resolveInspectorView({ ...base, panel: 'zones', hasPlan: false })).toBe('new-zones');
});
it('does not advertise zone editing for a source-only or read-only drawing', () => {
  expect(resolveInspectorView({ ...base, panel: 'zones', sourcePreview: true, hasPlan: false })).toBe('source');
});
