import { queryOptions } from '@tanstack/react-query';
import { api, type Project } from '@green/api-client';
import {
  hasNativeDxfMapSource,
  nativeDxfMapSource,
} from '../model/nativeDxfMapSource';

export { hasNativeDxfMapSource } from '../model/nativeDxfMapSource';

export function nativeDxfMapSourceQuery(project?: Project) {
  return queryOptions({
    queryKey: [
      'native-dxf-map-source',
      project?.id,
      project?.source_file?.content_sha256,
    ],
    enabled: hasNativeDxfMapSource(project),
    retry: false,
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      if (!project?.id) throw new Error('Проект ещё не загружен.');
      return nativeDxfMapSource(
        project,
        await api.getNativeDxfSourceAsset(project.id, signal),
      );
    },
  });
}
