import { describe, expect, it, vi } from 'vitest';
import { patternToolBindings } from './patternToolBindings';
import { brushToolBindings } from './brushToolBindings';
import { recommendationToolBindings } from './recommendationToolBindings';

describe('workspace placement bindings', () => {
  it('routes drawing cancellation through the zone owner without clearing placement fields', () => {
    const options = {
      placementAreaDrawing: true,
      cancelZoneDrawing: vi.fn(),
      previewPattern: { reset: vi.fn() },
      previewChanges: { reset: vi.fn() },
      setRowAxis: vi.fn(),
      editor: { setTool: vi.fn() },
    } as unknown as Parameters<typeof patternToolBindings>[0];
    patternToolBindings(options).onCancel();
    expect(options.cancelZoneDrawing).toHaveBeenCalledOnce();
    expect(options.previewPattern.reset).not.toHaveBeenCalled();
    expect(options.setRowAxis).not.toHaveBeenCalled();
    expect(options.editor.setTool).not.toHaveBeenCalled();
  });

  it('clears brush previews once before clearing strokes or ending the tool', () => {
    const events: string[] = [];
    const options = {
      previewBrush: { reset: () => events.push('brush') },
      previewChanges: { reset: () => events.push('changes') },
      setBrushStrokes: vi.fn(() => events.push('strokes')),
      editor: { setTool: vi.fn(() => events.push('select')) },
    } as unknown as Parameters<typeof brushToolBindings>[0];
    const actions = brushToolBindings(options);
    actions.onClear();
    expect(events).toEqual(['brush', 'changes', 'strokes']);
    expect(options.setBrushStrokes).toHaveBeenCalledWith([]);
    events.length = 0;
    actions.onCancel();
    expect(events).toEqual(['brush', 'changes', 'strokes', 'select']);
    expect(options.editor.setTool).toHaveBeenCalledWith('select');
  });

  it('reverses the row without mutating the existing axis and resets both previews', () => {
    const axis = {
      type: 'LineString',
      coordinates: [
        [1, 2],
        [3, 4],
      ],
    };
    const options = {
      rowAxis: axis,
      setRowAxis: vi.fn(),
      previewPattern: { reset: vi.fn() },
      previewChanges: { reset: vi.fn() },
    } as unknown as Parameters<typeof patternToolBindings>[0];
    patternToolBindings(options).onReverseAxis?.();
    expect(options.setRowAxis).toHaveBeenCalledExactlyOnceWith({
      type: 'LineString',
      coordinates: [
        [3, 4],
        [1, 2],
      ],
    });
    expect(axis.coordinates).toEqual([
      [1, 2],
      [3, 4],
    ]);
    expect(options.previewPattern.reset).toHaveBeenCalledTimes(1);
    expect(options.previewChanges.reset).toHaveBeenCalledTimes(1);
  });

  it('ends row drawing before starting a different axis mode', () => {
    const events: string[] = [];
    const options = {
      mapViewport: { current: { abortRowDrawing: () => events.push('abort') } },
      setRowInputMode: vi.fn(() => events.push('mode')),
      setRowDrawingPoints: vi.fn(),
      previewPattern: { reset: () => events.push('pattern') },
      previewChanges: { reset: () => events.push('changes') },
    } as unknown as Parameters<typeof patternToolBindings>[0];
    patternToolBindings(options).onAxisModeChange?.('draw');
    expect(events).toEqual(['abort', 'mode', 'pattern', 'changes']);
    expect(options.setRowInputMode).toHaveBeenCalledWith('draw');
    expect(options.setRowDrawingPoints).toHaveBeenCalledWith(0);
  });

  it('sends both recommendation branches through the same project version and map transition', () => {
    const options = {
      sceneOpen: true,
      changeMapMode: vi.fn(),
      previewRecommendation: { mutate: vi.fn() },
      project: { plan: { version: 7 } },
    } as unknown as Parameters<typeof recommendationToolBindings>[0];
    const actions = recommendationToolBindings(options);
    actions.onPreview({
      profile: 'balanced',
      max_sites: 40,
      zone_ids: ['west'],
    });
    actions.onScreenPreview?.({
      max_sites: null,
      screen_side: 'perimeter',
      zone_ids: ['west'],
    });
    expect(options.changeMapMode).toHaveBeenNthCalledWith(1, '2d');
    expect(options.changeMapMode).toHaveBeenNthCalledWith(2, '2d');
    expect(options.previewRecommendation.mutate).toHaveBeenNthCalledWith(1, {
      profile: 'balanced',
      max_sites: 40,
      zone_ids: ['west'],
      base_plan_version: 7,
    });
    expect(options.previewRecommendation.mutate).toHaveBeenNthCalledWith(2, {
      max_sites: null,
      screen_side: 'perimeter',
      zone_ids: ['west'],
      base_plan_version: 7,
    });
  });

  it('cancels recommendations once and returns to selection without changing the draft', () => {
    const options = {
      previewRecommendation: { reset: vi.fn() },
      setRecommendationOpen: vi.fn(),
      editor: { setTool: vi.fn() },
    } as unknown as Parameters<typeof recommendationToolBindings>[0];
    recommendationToolBindings(options).onCancel();
    expect(options.previewRecommendation.reset).toHaveBeenCalledTimes(1);
    expect(options.setRecommendationOpen).toHaveBeenCalledWith(false);
    expect(options.editor.setTool).toHaveBeenCalledWith('select');
  });
});
