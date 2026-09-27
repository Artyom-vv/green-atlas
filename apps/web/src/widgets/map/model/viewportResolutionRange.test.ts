import { describe, expect, it } from 'vitest';
import { withViewportContext } from './viewportGeometryContext';
import { bufferedMapRequest, viewportCovered } from './viewportRequest';
import {
  resolutionInRange,
  viewportResolutionRange,
} from './viewportResolutionRange';

const context = {
  projectId: 'a',
  geometryVersion: 1,
  sourceKey: 'source',
  resolution: 0.2,
};
const exact = { min: 0, max: 0.75, min_inclusive: false, max_inclusive: true };
const geometry = (range: unknown = exact) =>
  withViewportContext(
    {
      metadata: {
        representation_id: 'opaque-server-id',
        geometry_version: 1,
        resolution_range: range,
      },
    },
    context,
  );
const extent: [number, number, number, number] = [0, 0, 100, 100];

describe('server-declared resolution coverage', () => {
  it('honours inclusive and exclusive boundaries without parsing the ID', () => {
    const range = viewportResolutionRange(geometry(), context)!;
    expect(resolutionInRange(0, range)).toBe(false);
    expect(resolutionInRange(0.75, range)).toBe(true);
    expect(resolutionInRange(0.7500001, range)).toBe(false);
    const overview = {
      min: 2.2,
      max: 2.21,
      min_inclusive: false,
      max_inclusive: false,
    };
    expect(resolutionInRange(2.2, overview)).toBe(false);
    expect(resolutionInRange(2.2001, overview)).toBe(true);
    expect(resolutionInRange(2.21, overview)).toBe(false);
  });

  it.each([
    { projectId: 'b' },
    { sourceKey: 'restored' },
    { geometryVersion: 2 },
    { resolution: 0.3 },
  ])('rejects coverage from another query context %j', (change) => {
    expect(
      viewportResolutionRange(geometry(), { ...context, ...change }),
    ).toBeUndefined();
  });

  it.each([
    null,
    {},
    { ...exact, max: Infinity },
    { ...exact, min: 1 },
    { ...exact, max_inclusive: 'true' },
  ])('rejects missing or invalid range %j', (range) => {
    expect(viewportResolutionRange(geometry(range), context)).toBeUndefined();
  });

  it('keeps spatial coverage while using only the advertised zoom range', () => {
    const previous = bufferedMapRequest('a', extent, 0.2);
    const complete = { resolutionRange: exact };
    expect(viewportCovered(previous, 'a', extent, 0.7, complete)).toBe(true);
    expect(viewportCovered(previous, 'a', extent, 0.7501, complete)).toBe(
      false,
    );
    expect(
      viewportCovered(previous, 'a', [500, 0, 600, 100], 0.2, complete),
    ).toBe(false);
    expect(viewportCovered(previous, 'b', extent, 0.2, complete)).toBe(false);
    expect(viewportCovered(previous, 'a', extent, 0.21)).toBe(false);
    expect(viewportCovered(previous, 'a', extent, 0.2)).toBe(true);
  });

  it('does not round a requested resolution across the server boundary', () => {
    expect(bufferedMapRequest('a', extent, 0.75001).resolution).toBe(0.75001);
  });
});
