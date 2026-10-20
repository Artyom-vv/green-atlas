import { describe, expect, it } from 'vitest';
import { ideTabForTool } from './workspacePresentation';

describe('workspace contextual tab', () => {
  it('keeps placement on its tool tab through drawing, review and return', () => {
    expect(ideTabForTool('pattern_fill')).toBe('tool');
    expect(ideTabForTool('draw_area', true)).toBe('tool');
    expect(ideTabForTool('select', true)).toBe('tool');
    expect(ideTabForTool('pattern_fill')).toBe('tool');
  });

  it('preserves the inspector for drawing and review from the zone manager', () => {
    expect(ideTabForTool('draw_area')).toBe('inspector');
    expect(ideTabForTool('select')).toBe('inspector');
  });
});
