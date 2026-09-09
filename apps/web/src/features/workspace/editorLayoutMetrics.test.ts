import { expect, it } from 'vitest';
import { editorLayoutMetrics } from './editorLayoutMetrics';
it('responds to container size without freezing the sidebar at a desktop pixel width', () => {
  const wide = editorLayoutMetrics({ width: 1440, height: 800, rem: 16 });
  const narrow = editorLayoutMetrics({ width: 1000, height: 700, rem: 16 });
  expect(wide.width).toBe(374);
  expect(narrow.width).toBe(304);
  expect(editorLayoutMetrics({ width: 1200, height: 800, rem: 16 }, { width: .35 }).width).toBe(420);
  expect(editorLayoutMetrics({ width: 1000, height: 800, rem: 16 }, { width: .35 }).width).toBe(350);
});
it('keeps minimums readable at larger text sizes and fits short viewports', () => {
  const enlarged = editorLayoutMetrics({ width: 1200, height: 400, rem: 20 }, { projectHeight: .9 });
  expect(enlarged.minWidth).toBe(380);
  expect(enlarged.projectHeight).toBeLessThanOrEqual(enlarged.maxProject);
  const small = editorLayoutMetrics({ width: 320, height: 300, rem: 16 });
  expect(small.width).toBeLessThanOrEqual(320 - 64);
});
