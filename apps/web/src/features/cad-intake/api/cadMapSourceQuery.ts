import { queryOptions } from '@tanstack/react-query';
import { api, type Project } from '@green/api-client';
import { cadMapSource, cadSourceReceipt } from '../model/resolveCadMapSource';

export const hasCadMapSource = (project?: Project) =>
  project?.import_status?.mode === 'cad_preview' &&
  Boolean(project.source_file?.content_sha256);

export function cadMapSourceQuery(project?: Project) {
  return queryOptions({
    queryKey: [
      'workspace-cad-source',
      project?.id,
      project?.source_file?.content_sha256,
    ],
    enabled: hasCadMapSource(project),
    retry: false,
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      if (!project?.id) throw new Error('Проект ещё не загружен.');
      const operation = await api.getLatestOperation(
        project.id,
        'prepare_cad_preview',
        signal,
      );
      const receipt = cadSourceReceipt(project, operation);
      const asset = await api.getCadSourceAsset(
        project.id,
        receipt.intake_operation_id,
        signal,
      );
      return cadMapSource(project, operation, asset);
    },
  });
}
