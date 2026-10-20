import {
  api,
  type RenderCamera,
  type SceneSnapshot,
  type SceneRenderPreparation,
} from '@green/api-client';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';

export const sceneRenderFileUrl = api.downloadUrl;
export const sceneRenderActions = {
  create: api.createSceneRender,
  prepare: api.prepareSceneRender,
  read: api.getSceneRender,
  cancel: api.cancelSceneRender,
};

export function useSceneRender(
  snapshot: SceneSnapshot | undefined,
  getCamera: () => RenderCamera | undefined,
  preparation?: SceneRenderPreparation,
) {
  const projectId = snapshot?.project_id ?? '';
  const submittedView = useRef<string | undefined>(undefined);
  const [renderContext, setRenderContext] = useState<{
    horizonYear: number;
    summary: SceneRenderPreparation['summary'];
  }>();
  const viewKey = (camera: RenderCamera) =>
    JSON.stringify([
      snapshot?.content_sha256,
      snapshot?.horizon_year,
      preparation?.id,
      camera,
    ]);
  const start = useMutation({
    mutationFn: async () => {
      const camera = getCamera();
      if (!snapshot || !camera || !snapshot.content_sha256)
        throw new Error('Сцена ещё не готова');
      if (
        preparation &&
        (preparation.project_id !== projectId ||
          preparation.scene_sha256 !== snapshot.content_sha256 ||
          preparation.horizon_year !== snapshot.horizon_year)
      )
        throw new Error('Подготовьте окружение текущей сцены');
      const submittedKey = viewKey(camera);
      const previewScene =
        preparation?.provenance?.scope ===
        'display-only; full render preparation is explicit';
      const frozen =
        preparation && !previewScene
          ? preparation
          : await sceneRenderActions.prepare(projectId, {
              request_id: crypto.randomUUID(),
              scene_sha256: snapshot.content_sha256,
              horizon_year: snapshot.horizon_year,
              camera,
              preview_scene: previewScene,
            });
      const result = await sceneRenderActions.create(projectId, {
        request_id: crypto.randomUUID(),
        scene_sha256: frozen.scene_sha256,
        horizon_year: snapshot.horizon_year,
        camera: previewScene ? frozen.camera : camera,
        resolution: 1280,
        preparation_id: frozen.id,
        preparation_sha256: frozen.sha256,
      });
      submittedView.current = submittedKey;
      setRenderContext({
        horizonYear: snapshot.horizon_year,
        summary: frozen.summary,
      });
      return result;
    },
    retry: false,
  });
  const status = useQuery({
    queryKey: ['scene-render', projectId, start.data?.id],
    queryFn: ({ signal }) =>
      sceneRenderActions.read(projectId, start.data!.id, { signal }),
    enabled: !!start.data,
    initialData: start.data,
    refetchInterval: (query) =>
      ['queued', 'rendering'].includes(query.state.data?.status ?? '')
        ? 1000
        : false,
  });
  const job = status.data;
  const busy =
    start.isPending || job?.status === 'queued' || job?.status === 'rendering';
  const cancel = useMutation({
    mutationFn: () => sceneRenderActions.cancel(projectId, job!.id),
    onSuccess: () => void status.refetch(),
  });
  const error = start.error || status.error || cancel.error;
  const matchesCurrentView = () => {
    const camera = getCamera();
    return Boolean(camera && submittedView.current === viewKey(camera));
  };
  return {
    start,
    status,
    cancel,
    job,
    busy,
    error,
    matchesCurrentView,
    renderContext,
  };
}
