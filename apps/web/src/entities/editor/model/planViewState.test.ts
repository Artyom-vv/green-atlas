import { describe, expect, it } from 'vitest';
import {
  PLAN_VIEW_STATE_ATTRIBUTES,
  sourceCenterToWorld,
  validPlanViewState,
  worldCenterToSource,
  writePlanViewStateAttributes,
} from '@/entities/editor/model/planViewState';

describe('PlanViewState coordinate bridge', () => {
  it('round-trips an absolute DXF centre through the shifted Three.js basis', () => {
    const source: [number, number] = [37_512.25, 18_407.75];
    const origin = [37_000, 18_000];
    const world = sourceCenterToWorld(source, origin);
    expect(world).toEqual([512.25, -407.75]);
    expect(worldCenterToSource(world[0], world[1], origin)).toEqual(source);
  });

  it('rejects camera state before a viewport has a usable CSS size', () => {
    expect(
      validPlanViewState({
        center: [1, 2],
        resolution: 0.5,
        rotation: 0,
        viewport: [0, 600],
      }),
    ).toBe(false);
    expect(
      validPlanViewState({
        center: [1, 2],
        resolution: 0.5,
        rotation: 0,
        viewport: [800, 600],
      }),
    ).toBe(true);
  });

  it('publishes signed centres and rotation for a real browser round-trip audit', () => {
    const element = document.createElement('div');
    writePlanViewStateAttributes(element, {
      center: [-571.7295, -51.602],
      resolution: 5.13591625,
      rotation: -0.425,
      viewport: [856, 668],
    });
    expect(element.getAttribute(PLAN_VIEW_STATE_ATTRIBUTES.centerX)).toBe(
      '-571.7295',
    );
    expect(element.getAttribute(PLAN_VIEW_STATE_ATTRIBUTES.centerY)).toBe(
      '-51.602',
    );
    expect(element.getAttribute(PLAN_VIEW_STATE_ATTRIBUTES.resolution)).toBe(
      '5.13591625',
    );
    expect(element.getAttribute(PLAN_VIEW_STATE_ATTRIBUTES.rotation)).toBe(
      '-0.425',
    );
    expect(element.getAttribute(PLAN_VIEW_STATE_ATTRIBUTES.viewportWidth)).toBe(
      '856',
    );
    expect(
      element.getAttribute(PLAN_VIEW_STATE_ATTRIBUTES.viewportHeight),
    ).toBe('668');
  });
});
