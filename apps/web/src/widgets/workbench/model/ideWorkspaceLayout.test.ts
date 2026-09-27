import { describe, expect, it } from 'vitest';
import {
  ideWorkspaceLayout,
  readIdeWorkspacePreference,
} from './ideWorkspaceLayout';

describe('IDE workspace constraints', () => {
  it('keeps the map usable at 1280 and 1024 without losing preferred sizes', () => {
    const preference = {
      resourcesWidth: 250,
      rightWidth: 420,
      resultsHeight: 240,
    };
    const before = { ...preference };
    const wide = ideWorkspaceLayout({ width: 1600, height: 800 }, preference);
    const medium = ideWorkspaceLayout({ width: 1280, height: 800 }, preference);
    const narrow = ideWorkspaceLayout({ width: 1024, height: 700 }, preference);
    expect(wide).toMatchObject({ resourcesWidth: 250, rightWidth: 420 });
    expect(medium.mapWidth).toBeGreaterThanOrEqual(640);
    expect(narrow).toMatchObject({
      resourcesWidth: 40,
      resourcesAutoCollapsed: true,
      rightExpanded: true,
    });
    expect(narrow.mapWidth).toBeGreaterThanOrEqual(640);
    expect(preference).toEqual(before);
    expect(
      ideWorkspaceLayout({ width: 1600, height: 800 }, preference),
    ).toEqual(wide);
  });

  it('temporarily makes room for explicitly opened resources on a narrow window', () => {
    const result = ideWorkspaceLayout(
      { width: 1024, height: 700 },
      {},
      {
        resourcesOpen: true,
        rightOpen: true,
        resultsOpen: false,
        priority: 'resources',
      },
    );
    expect(result).toMatchObject({
      resourcesExpanded: true,
      resourcesWidth: 220,
      rightAutoCollapsed: true,
      rightWidth: 40,
    });
    expect(result.mapWidth).toBeGreaterThanOrEqual(640);
  });

  it('does not reopen an explicitly closed pane when width is restored', () => {
    const result = ideWorkspaceLayout(
      { width: 1600, height: 800 },
      {},
      {
        resourcesOpen: false,
        rightOpen: false,
        resultsOpen: false,
        priority: 'right',
      },
    );
    expect(result).toMatchObject({
      resourcesWidth: 40,
      rightWidth: 40,
      resourcesAutoCollapsed: false,
      rightAutoCollapsed: false,
      resultsTotalHeight: 40,
    });
  });

  it('bounds result height without consuming the map on short windows', () => {
    for (const height of [300, 400, 700, 1000]) {
      const result = ideWorkspaceLayout(
        { width: 1280, height },
        { resultsHeight: 600 },
        {
          resourcesOpen: true,
          rightOpen: true,
          resultsOpen: true,
          priority: 'right',
        },
      );
      expect(height - result.resultsTotalHeight).toBeGreaterThanOrEqual(260);
      expect(result.resultsHeight).toBeLessThanOrEqual(height * 0.45);
    }
  });

  it('validates stored preferences and reports physically constrained small screens', () => {
    expect(
      readIdeWorkspacePreference({
        resourcesWidth: -5,
        rightWidth: Infinity,
        resultsHeight: '220',
        unrelated: 100,
      }),
    ).toEqual({ resourcesWidth: 180 });
    expect(readIdeWorkspacePreference([220, 340])).toEqual({});
    const tiny = ideWorkspaceLayout({ width: 320, height: 300 });
    expect(tiny.mapWidth).toBeGreaterThanOrEqual(0);
    expect(tiny.resourcesWidth + tiny.rightWidth + tiny.mapWidth).toBe(320);
    expect(tiny.mapMinimumSatisfied).toBe(false);
  });
});
