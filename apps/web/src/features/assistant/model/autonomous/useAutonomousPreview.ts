import { zoneChangeSummary } from '@/entities/planting-zone/model/zoneChangeSummary';
import {
  api,
  type AgentRun,
  type ChangeSetPreview,
  type ZoneChangePreview,
} from '@green/api-client';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

export type AutonomousPreview = {
  runId: string;
  projectId: string;
  stateVersion: number;
} & (
  | { kind?: 'plantings'; preview: ChangeSetPreview }
  | { kind: 'planting_zones'; preview: ZoneChangePreview }
);
type SavedPreview =
  | { kind: 'plantings'; preview: ChangeSetPreview }
  | { kind: 'planting_zones'; preview: ZoneChangePreview };

/** The server returns the saved geometry; compact tool summaries cannot draw it. */
export function useAutonomousPreview(
  run: AgentRun | undefined,
  projectId: string,
  onPreviewChange?: (value: AutonomousPreview | undefined) => void,
) {
  const [now, setNow] = useState(Date.now);
  const previewRef = run?.state.pending_approval?.preview_ref;
  const kind = run?.state.pending_approval?.kind ?? 'plantings';
  const supported = kind === 'plantings' || kind === 'planting_zones';
  const enabled =
    run?.state.project_id === projectId &&
    run.state.status === 'waiting_approval' &&
    Boolean(previewRef);
  const query = useQuery({
    queryKey: ['agent-preview', projectId, run?.state.run_id, previewRef, kind],
    queryFn: async (): Promise<SavedPreview> => {
      if (kind !== 'planting_zones')
        return {
          kind: 'plantings',
          preview: await api.getAgentRunPreview(
            projectId,
            run!.state.run_id,
            previewRef!,
          ),
        };
      const preview = await api.getAgentRunZonePreview(
        projectId,
        run!.state.run_id,
        previewRef!,
      );
      if (
        preview.project_id !== projectId ||
        preview.base_state_version !== run!.state.snapshot_version ||
        (preview.base_plan_version ?? null) !==
          (run!.state.plan_version ?? null) ||
        !zoneChangeSummary(preview)
      ) {
        throw new Error(
          'Подробности участка не соответствуют предложению. Рассчитайте его заново.',
        );
      }
      return { kind: 'planting_zones', preview };
    },
    enabled:
      enabled &&
      supported &&
      (Boolean(onPreviewChange) || kind === 'planting_zones'),
    retry: false,
    staleTime: 0,
  });
  const expiry = query.data
    ? Date.parse(query.data.preview.expires_at)
    : undefined;
  const expired =
    expiry !== undefined && (!Number.isFinite(expiry) || expiry <= now);
  useEffect(() => {
    if (expiry === undefined || !Number.isFinite(expiry)) return;
    const timer = window.setTimeout(
      () => setNow(Date.now()),
      Math.max(0, Math.min(2147483647, expiry - Date.now())),
    );
    return () => window.clearTimeout(timer);
  }, [expiry]);
  const saved =
    enabled && supported && !query.isError && !expired ? query.data : undefined;
  const preview = saved?.preview;
  const runId = run?.state.run_id;
  const stateVersion = run?.state.snapshot_version;
  useEffect(() => {
    onPreviewChange?.(
      saved && runId && stateVersion !== undefined
        ? saved.kind === 'planting_zones'
          ? {
              runId,
              projectId,
              stateVersion,
              kind: 'planting_zones',
              preview: saved.preview,
            }
          : { runId, projectId, stateVersion, preview: saved.preview }
        : undefined,
    );
    return () => onPreviewChange?.(undefined);
  }, [onPreviewChange, saved, projectId, runId, stateVersion]);
  return {
    preview,
    zonePreview: saved?.kind === 'planting_zones' ? saved.preview : undefined,
    loading: enabled && supported && query.isPending,
    error: enabled
      ? !supported
        ? new Error('Тип предложения недоступен. Рассчитайте его заново.')
        : expired
          ? new Error('Предложение устарело. Рассчитайте его заново.')
          : query.error
      : null,
  };
}
