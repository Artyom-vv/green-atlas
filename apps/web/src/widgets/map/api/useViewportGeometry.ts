import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { readViewportGeometry } from '@/entities/source-data/api/readViewportGeometry';
import type { MapExtent } from '../model/mapContracts';
import { withViewportContext } from '../model/viewportGeometryContext';
import { viewportResolutionRange } from '../model/viewportResolutionRange';
import {
  bufferedMapRequest,
  viewportCovered,
  VIEWPORT_REQUEST_POLICY,
  type BufferedMapRequest,
} from '../model/viewportRequest';

interface MapGeometryMetadata {
  returned_features?: number;
  total_matches?: number;
  truncated?: boolean;
}

export interface ViewportGeometryOptions {
  projectId: string;
  geometryVersion?: number;
  sourceKey?: string;
  enabled?: boolean;
}

/** Query owns geometry. Local state only identifies the latest settled viewport. */
export function useViewportGeometry({
  projectId,
  geometryVersion,
  sourceKey,
  enabled = true,
}: ViewportGeometryOptions) {
  const client = useQueryClient();
  const requestRef = useRef<BufferedMapRequest | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [request, setRequest] = useState<BufferedMapRequest>();
  const active = request?.projectId === projectId ? request : undefined;
  useEffect(() => {
    if (!enabled)
      void client.cancelQueries({ queryKey: ['map-features', projectId] });
  }, [client, enabled, projectId]);
  useEffect(
    () => () => {
      clearTimeout(timer.current);
      requestRef.current = undefined;
    },
    [projectId],
  );
  const query = useQuery({
    queryKey: [
      'map-features',
      projectId,
      geometryVersion,
      sourceKey,
      active?.extent,
      active?.resolution,
    ],
    queryFn: async ({ signal }) => {
      if (!active) throw new Error('Map viewport is not ready');
      const snapshot = await readViewportGeometry(
        projectId,
        active.extent,
        active.resolution,
        signal,
      );
      return {
        ...snapshot,
        feature_collection: withViewportContext(snapshot.feature_collection, {
          projectId,
          geometryVersion,
          sourceKey,
          resolution: active.resolution,
        }),
        loadedGeometryVersion: geometryVersion,
      };
    },
    enabled: Boolean(enabled && projectId && active),
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === projectId &&
      previousQuery.queryKey[3] === sourceKey
        ? previous
        : undefined,
    staleTime: VIEWPORT_REQUEST_POLICY.cacheMs,
  });
  const metadata = (
    query.data?.feature_collection as
      { metadata?: MapGeometryMetadata } | undefined
  )?.metadata;
  const range =
    active && !query.isPlaceholderData
      ? viewportResolutionRange(query.data?.feature_collection, {
          projectId,
          geometryVersion,
          sourceKey,
          resolution: active.resolution,
        })
      : undefined;
  const complete = !query.isPlaceholderData && metadata?.truncated === false;
  const onExtentChange = useCallback(
    (extent: MapExtent, resolution: number) => {
      if (
        viewportCovered(
          requestRef.current,
          projectId,
          extent,
          resolution,
          requestRef.current === active && complete
            ? { resolutionRange: range }
            : undefined,
        )
      )
        return;
      const next = bufferedMapRequest(projectId, extent, resolution);
      requestRef.current = next;
      clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = undefined;
        void client
          .cancelQueries({ queryKey: ['map-features', projectId] })
          .then(() => {
            if (requestRef.current === next) setRequest(next);
          });
      }, VIEWPORT_REQUEST_POLICY.settleMs);
    },
    [client, projectId, active, range, complete],
  );
  return {
    query,
    metadata: enabled ? metadata : undefined,
    delivery: enabled ? query.data : undefined,
    onExtentChange,
  };
}
