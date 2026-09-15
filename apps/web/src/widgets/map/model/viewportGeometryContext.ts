export interface ViewportGeometryContext {
  projectId: string;
  geometryVersion?: number;
  sourceKey?: string;
  resolution: number;
}

/** Local query provenance, not an additional server field or source of truth. */
export function withViewportContext(
  geometry: Record<string, unknown>,
  context: ViewportGeometryContext,
): Record<string, unknown> {
  return { ...geometry, viewportContext: context };
}

export function viewportGeometryIdentity(
  geometry: Record<string, unknown>,
  geometryRevision?: number,
) {
  const context = geometry.viewportContext as
    ViewportGeometryContext | undefined;
  const metadata = (geometry.metadata ?? {}) as Record<string, unknown>;
  return {
    // A restored release may reuse a geometry version. Include its source
    // identity and both requested/returned versions to avoid a false cache hit.
    scope: JSON.stringify([
      context?.projectId,
      context?.geometryVersion,
      context?.sourceKey,
      geometryRevision,
      metadata.geometry_version,
      metadata.source_revision,
      metadata.source_hash,
    ]),
    representation:
      typeof metadata.representation_id === 'string' &&
      metadata.representation_id.length > 0
        ? JSON.stringify(['canonical', metadata.representation_id])
        : JSON.stringify([
            'legacy',
            metadata.resolution ?? context?.resolution,
            metadata.lod,
            metadata.simplify_tolerance,
          ]),
  };
}
