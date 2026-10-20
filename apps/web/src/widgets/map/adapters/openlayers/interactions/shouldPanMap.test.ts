import { describe, expect, it } from 'vitest';
import type MapBrowserEvent from 'ol/MapBrowserEvent';
import { shouldPanMap } from './shouldPanMap';

const event = (button: number) =>
  ({ originalEvent: { button } }) as MapBrowserEvent;

describe('map pan gesture', () => {
  it('allows ordinary navigation with the default selection tool', () => {
    expect(shouldPanMap(event(0), 'select', false)).toBe(true);
    expect(shouldPanMap(event(0), 'pan', false)).toBe(true);
    expect(shouldPanMap(event(2), 'select', false)).toBe(false);
    expect(shouldPanMap(event(2), 'pan', false)).toBe(false);
  });

  it('leaves drawing and box-selection drags to their own tools', () => {
    expect(shouldPanMap(event(0), 'draw_area', false)).toBe(false);
    expect(shouldPanMap(event(0), 'select_box', false)).toBe(false);
    expect(shouldPanMap(event(0), 'move', false)).toBe(false);
  });

  it('keeps middle-button and space navigation', () => {
    expect(shouldPanMap(event(1), 'select', false)).toBe(true);
    expect(shouldPanMap(event(0), 'select', true)).toBe(true);
    for (const tool of [
      'draw_area',
      'select_box',
      'select_lasso',
      'move',
      'pattern_row',
      'brush',
      'add_tree',
    ]) {
      expect(shouldPanMap(event(0), tool, true)).toBe(true);
      expect(shouldPanMap(event(1), tool, false)).toBe(true);
      expect(shouldPanMap(event(2), tool, true)).toBe(false);
    }
  });
  it.each(['add_tree', 'add_shrub', 'pattern_fill', 'copy'])(
    'allows ordinary drag between clicks with %s',
    (tool) => {
      expect(shouldPanMap(event(0), tool, false)).toBe(true);
    },
  );
});
