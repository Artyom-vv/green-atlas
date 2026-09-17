import { useEffect, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type Project, type ProjectOperation } from '@green/api-client';

export function useOpenCadPreview(
  projectId: string | undefined,
  onNavigate: (path: string) => void,
) {
  const client = useQueryClient();
  const mounted = useRef(true);
  const currentProject = useRef(projectId);
  useEffect(() => {
    currentProject.current = projectId;
  }, [projectId]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  return useMutation({
    mutationFn: async (operation: ProjectOperation) => {
      const prepared = operation.kind === 'prepare_cad_project';
      const receipt = prepared
        ? operation.cad_prepare?.result
        : operation.cad_preview?.result;
      if (operation.status !== 'completed' || !receipt)
        throw new Error('Подготовка карты ещё не завершена.');
      const project = await api.getProject(operation.project_id, false);
      if (
        (project.state_version ?? 0) < receipt.published_state_version ||
        project.source_file?.content_sha256 !==
          ('source_sha256' in receipt
            ? receipt.source_sha256
            : receipt.output_sha256) ||
        project.import_status?.mode !==
          (prepared ? 'source_dxf' : 'cad_preview')
      )
        throw new Error(
          'Источник проекта изменился. Обновите страницу, чтобы открыть текущие данные.',
        );
      return project;
    },
    onSuccess: async (project) => {
      await Promise.all(
        ['setup-project', 'workspace-project'].map((name) =>
          client.cancelQueries({ queryKey: [name, project.id], exact: true }),
        ),
      );
      for (const name of ['setup-project', 'workspace-project'])
        client.setQueryData<Project>([name, project.id], (current) =>
          (current?.state_version ?? 0) > (project.state_version ?? 0)
            ? current
            : project,
        );
      void client.invalidateQueries({ queryKey: ['projects'] });
      void client.invalidateQueries({
        queryKey: ['data-passport', project.id],
      });
      if (mounted.current && currentProject.current === project.id)
        onNavigate(`/projects/${project.id}/workspace`);
    },
  });
}
