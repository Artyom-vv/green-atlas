import { describe, expect, it } from 'vitest';
import {
  viewportGeometryIdentity,
  withViewportContext,
} from './viewportGeometryContext';

const snapshot = (metadata = {}, sourceKey = 'source') =>
  withViewportContext(
    {
      metadata: {
        representation_id: 'viewport-v1:tol=0.000:forbidden=1',
        geometry_version: 1,
        resolution: 0.2,
        lod: 'detail',
        ...metadata,
      },
    },
    { projectId: 'project', geometryVersion: 1, sourceKey, resolution: 0.2 },
  );

describe('viewport representation identity', () => {
  it('separates display completeness and raw zoom from canonical geometry', () => {
    expect(viewportGeometryIdentity(snapshot())).toEqual(
      viewportGeometryIdentity(
        snapshot({ resolution: 0.7, lod: 'budgeted', truncated: true }),
      ),
    );
  });

  it('keeps source and geometry versions independent of representation', () => {
    const original = viewportGeometryIdentity(snapshot(), 1);
    const restored = viewportGeometryIdentity(snapshot({}, 'replacement'), 1);
    const revised = viewportGeometryIdentity(snapshot(), 2);
    expect(restored.scope).not.toBe(original.scope);
    expect(revised.scope).not.toBe(original.scope);
    expect(restored.representation).toBe(original.representation);
  });

  it.each([undefined, null, '', 3])(
    'retains strict legacy fallback for ID %j',
    (id) => {
      const original = viewportGeometryIdentity(
        snapshot({ representation_id: id }),
      );
      const zoomed = viewportGeometryIdentity(
        snapshot({ representation_id: id, resolution: 0.7 }),
      );
      expect(zoomed.representation).not.toBe(original.representation);
    },
  );
});
