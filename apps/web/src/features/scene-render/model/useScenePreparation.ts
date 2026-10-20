import { api, type RenderCamera, type SceneSnapshot } from '@green/api-client';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

export const scenePreparationActions = { prepare: api.prepareSceneRender };

// A local request clock belongs to its cached operation, not a mounted notice.
// Weak keys disappear with the query; no timestamp crosses scene revisions.
const requestStarts = new WeakMap<object, number>();

export function useScenePreparation({
  snapshot,
  enabled,
  getCamera,
}: {
  snapshot?: SceneSnapshot;
  enabled: boolean;
  getCamera: () => RenderCamera | undefined;
}) {
  const queryClient = useQueryClient();
  const sceneKey = JSON.stringify([
    snapshot?.project_id,
    snapshot?.content_sha256,
    snapshot?.horizon_year,
    snapshot?.geometry_version,
    snapshot?.plan_version,
  ]);
  const [queuedFor, setQueuedFor] = useState<string>();
  const [requestClock, setRequestClock] = useState<{
    scene: string;
    startedAt: number;
  }>();
  const queryKey = [
    'scene-preparation',
    snapshot?.project_id,
    snapshot?.content_sha256,
    snapshot?.horizon_year,
    snapshot?.geometry_version,
    snapshot?.plan_version,
  ];
  const query = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const camera = getCamera();
      if (!snapshot?.project_id || !snapshot.content_sha256 || !camera)
        throw new Error('3D-сцена ещё не готова');
      const operation = queryClient
        .getQueryCache()
        .find({ queryKey, exact: true });
      const startedAt = Date.now();
      if (operation) requestStarts.set(operation, startedAt);
      setRequestClock({ scene: sceneKey, startedAt });
      const result = await scenePreparationActions.prepare(
        snapshot.project_id,
        {
          request_id: crypto.randomUUID(),
          preview_scene: false,
          scene_sha256: snapshot.content_sha256,
          horizon_year: snapshot.horizon_year,
          camera,
        },
        { signal },
      );
      if (
        result.project_id !== snapshot.project_id ||
        result.scene_sha256 !== snapshot.content_sha256 ||
        result.horizon_year !== snapshot.horizon_year ||
        result.geometry_version !== snapshot.geometry_version ||
        result.plan_version !== snapshot.plan_version
      )
        throw new Error(
          'Окружение относится к другой версии сцены. Повторите подготовку',
        );
      return result;
    },
    enabled:
      enabled && Boolean(snapshot?.project_id && snapshot?.content_sha256),
    retry: false,
    staleTime: Infinity,
    gcTime: 60_000,
  });
  const { isFetching, refetch } = query;
  useEffect(() => {
    if (!queuedFor) return;
    if (queuedFor !== sceneKey) {
      setQueuedFor(undefined);
      return;
    }
    if (enabled && !isFetching) {
      setQueuedFor(undefined);
      void refetch();
    }
  }, [queuedFor, sceneKey, enabled, isFetching, refetch]);
  const preparation =
    query.data &&
    query.data.project_id === snapshot?.project_id &&
    query.data.scene_sha256 === snapshot?.content_sha256 &&
    query.data.horizon_year === snapshot?.horizon_year &&
    query.data.geometry_version === snapshot?.geometry_version &&
    query.data.plan_version === snapshot?.plan_version
      ? query.data
      : undefined;
  const operation = queryClient.getQueryCache().find({ queryKey, exact: true });
  const loading = query.isFetching || queuedFor === sceneKey;
  return {
    preparation,
    startedAt:
      requestClock?.scene === sceneKey
        ? requestClock.startedAt
        : operation
          ? requestStarts.get(operation)
          : undefined,
    loading,
    error: loading
      ? null
      : query.error ||
        (query.data && !preparation
          ? new Error(
              'Окружение относится к другой версии сцены. Повторите подготовку',
            )
          : null),
    // Refetch the same revision: keep its last successful preparation visible
    // throughout refresh and after a failed request. Revision keys never share it.
    refresh: () => {
      if (!enabled) return;
      if (query.isFetching) setQueuedFor(sceneKey);
      else void query.refetch();
    },
  };
}
