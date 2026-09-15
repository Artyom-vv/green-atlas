import { useCallback, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { LayerKind, Project } from '@green/api-client';
import {
  cadMapSourceQuery,
  hasCadMapSource,
} from '@/features/cad-intake/api/cadMapSourceQuery';
import type {
  CadRenderState,
  MapCadSource,
} from '@/widgets/map/model/cadSource';
import {
  hasNativeDxfMapSource,
  nativeDxfMapSourceQuery,
} from '@/entities/source-data/api/nativeDxfMapSourceQuery';

export function useWorkspaceCadSource(project?: Project) {
  const native = hasNativeDxfMapSource(project);
  const eligible = native || hasCadMapSource(project);
  const previewQuery = useQuery(cadMapSourceQuery(project));
  const nativeQuery = useQuery(nativeDxfMapSourceQuery(project));
  const query = native ? nativeQuery : previewQuery;
  const [render, setRender] = useState<{
    url?: string;
    state: CadRenderState;
  }>();
  const source = useMemo<MapCadSource | undefined>(() => {
    if (!eligible || !query.data) return undefined;
    const layerRoles: Record<string, LayerKind> = {};
    for (const layer of project?.layers ?? []) {
      if (layer.mapped_kind) layerRoles[layer.source_name] = layer.mapped_kind;
    }
    return { ...query.data, layerRoles };
  }, [eligible, query.data, project?.layers]);
  const onRenderState = useCallback(
    (state: CadRenderState) => {
      setRender({ url: source?.url, state });
    },
    [source?.url],
  );
  const state = render?.url === source?.url ? render?.state : undefined;
  const failed = state?.status === 'error';
  return {
    source,
    onRenderState,
    pending: eligible && !query.error && !failed && state?.status !== 'ready',
    error: eligible ? query.error : undefined,
    // CAD owns display. Editable projects still need exact vector interaction data.
    vectorGeometryEnabled:
      native || !eligible || Boolean(query.error) || failed,
    baseReady: eligible && state?.status === 'ready',
  };
}
