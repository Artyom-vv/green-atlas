import { expect, it } from 'vitest';
import {
  viewportPresentationBindings,
  type ViewportPresentationProps,
} from './viewportPresentation';

it('shows chosen areas before a plan exists and keeps saved scope selection authoritative afterwards', () => {
  const geometry = {
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 10],
        [0, 0],
      ],
      [
        [2, 2],
        [2, 4],
        [4, 4],
        [4, 2],
        [2, 2],
      ],
    ],
  };
  const focus = { type: 'GeometryCollection', geometries: [] };
  const props = {
    draftZones: [{ id: 'new', geometry }],
    reviewZones: [],
    focusGeometry: focus,
    projectHasPlan: false,
    previewChanges: {},
    previewPattern: {},
    candidateInspection: {},
    planComparison: { mode: 'editing' },
  } as unknown as ViewportPresentationProps;
  const first = viewportPresentationBindings(props);
  expect(first.focusGeometry).toEqual({
    type: 'GeometryCollection',
    geometries: [geometry],
  });
  expect((first.focusGeometry as { geometries: unknown[] }).geometries[0]).toBe(
    geometry,
  );
  expect(
    viewportPresentationBindings({ ...props, projectHasPlan: true })
      .focusGeometry,
  ).toBe(focus);
});
